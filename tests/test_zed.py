import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch


HOME = Path(__file__).resolve().parents[1]


class ZedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.z = types.ModuleType("zed")
        self.z.__file__ = str(HOME / "bin/zed")
        with patch.dict(os.environ, {"ZED_PROFILE_DIR": str(self.root)}):
            exec(compile((HOME / "bin/zed").read_text(), self.z.__file__, "exec"), self.z.__dict__)
        (self.root / "profile.md").write_text("# Throwaway test profile\n")
        self.enterContext(patch.object(self.z, "core_sync_events", return_value=[]))  # would read the live repo
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(contextlib.redirect_stderr(self.output))

    def config(self, data):
        self.z.RUNTIMES_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.RUNTIMES_PATH.write_text(json.dumps(data))

    def both(self):
        self.config({"order": ["claude", "copilot"],
                     "runtimes": {"copilot": {"models": ["gpt-test", "claude-test-1.2"]}}})

    def test_legacy_profile_remains_claude_only(self):
        with patch.object(self.z.shutil, "which", return_value="/cli"):
            self.assertEqual(self.z.choose_runtime(), "claude")
            self.assertFalse(self.z.runtime_report("copilot")["enabled"])

    def test_single_runtime_profiles(self):
        for runtime in ("claude", "copilot"):
            with self.subTest(runtime=runtime):
                self.config({"order": [runtime]})
                with patch.object(self.z.shutil, "which", return_value="/cli"):
                    self.assertEqual(self.z.choose_runtime(), runtime)

    def test_fallback_skips_limited_disabled_and_missing(self):
        self.both()
        with patch.object(self.z.shutil, "which", return_value="/cli"):
            with patch.object(self.z, "runtime_state", return_value={"claude": {"until": "3pm"}}):
                self.assertEqual(self.z.choose_runtime(), "copilot")
                with self.assertRaises(SystemExit):
                    self.z.choose_runtime("claude")
            self.config({"order": ["claude", "copilot"], "runtimes": {"claude": {"enabled": False}}})
            self.assertEqual(self.z.choose_runtime(), "copilot")
        with patch.object(self.z.shutil, "which", return_value=None):
            with self.assertRaises(SystemExit):
                self.z.choose_runtime()

    def test_nearest_runtime_pid_wins_in_inherited_environment(self):
        env = {"CLAUDE_PID": "100", "CLAUDE_CODE_SESSION_ID": "claude-session",
               "COPILOT_LOADER_PID": "200", "HERDR_PANE_ID": "qa:p1"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(self.z, "ancestor_pids", return_value=[300, 200, 100]):
                self.assertEqual(self.z.me(), {"pane": "qa:p1", "runtime": "copilot",
                                              "agent_session": None, "pid": 200})
            with patch.object(self.z, "ancestor_pids", return_value=[300, 100, 200]):
                self.assertEqual(self.z.me()["runtime"], "claude")
                self.assertEqual(self.z.me()["agent_session"], "claude-session")

    def test_legacy_lock_identity(self):
        self.assertTrue(self.z.same_holder({"claude_session": "old"}, {"agent_session": "old"}))
        self.assertFalse(self.z.same_holder({"agent_session": "a", "pid": 1},
                                          {"agent_session": "b", "pid": 1}))
        self.assertTrue(self.z.same_holder({"pid": 2}, {"pid": 2}))

    def test_schema_migrates_old_sessions(self):
        self.z.DB_PATH.parent.mkdir()
        conn = sqlite3.connect(self.z.DB_PATH)
        conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")
        conn.close()
        conn = self.z.db()
        self.addCleanup(conn.close)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
        self.assertTrue({"runtime", "model", "effort", "brief", "credits", "external"} <= columns)
        self.assertTrue({"employee", "hat", "verdict", "origin", "direct"} <= columns)
        self.assertEqual(conn.execute("SELECT count(*) FROM employees").fetchone()[0], 0)
        self.z.db().close()  # migrating twice is harmless

    def test_employee_columns_default_to_no_employee(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO tasks (title, created_at, updated_at) VALUES ('t', 'now', 'now')")
        conn.execute("INSERT INTO sessions (task_id, repo, base, branch, worktree, agent, created_at, updated_at) "
                     "VALUES (1, 'r', 'b', 'br', 'w', 'a', 'now', 'now')")
        row = conn.execute("SELECT employee, hat, verdict, origin, direct FROM sessions").fetchone()
        self.assertEqual(tuple(row), (None, None, None, None, 0))

    def test_fixed_model_and_default(self):
        self.config({"order": ["copilot"], "runtimes": {"copilot": {"model": "gpt-test", "effort": "high"}}})
        self.assertEqual(self.z.pick_model("copilot")[:2], ("gpt-test", "high"))
        self.config({"order": ["copilot"]})
        self.assertEqual(self.z.pick_model("copilot")[:2], (None, None))

    def test_ranking_maps_models_and_reasoning_variants(self):
        self.both()
        cfg = json.loads(self.z.RUNTIMES_PATH.read_text())
        cfg["model_policy"] = {"method": "attractive-quadrant"}
        self.config(cfg)
        entries = [
            {"model": "gpt-test", "effort": "max", "intelligence": 100, "cost_per_task": 10},
            {"model": "gpt-test", "effort": "high", "intelligence": 90, "cost_per_task": 1},
            {"model": "claude-test-1.2", "effort": "high", "intelligence": 80, "cost_per_task": 2},
            {"model": "unavailable", "intelligence": 60, "cost_per_task": 1},
        ]
        ranking = {"imported_at": "2000-01-01", "entries": entries}
        with patch.object(self.z, "model_ranking", return_value=ranking):
            model, effort, why = self.z.pick_model("copilot")
            self.assertEqual((model, effort), ("gpt-test", "high"))
            self.assertIn("refresh", why)
            self.assertEqual(self.z.pick_model("claude")[:2], ("claude-test-1-2", "high"))

    def test_ranking_missing_and_empty_quadrant(self):
        self.config({"order": ["copilot"], "model_policy": {"method": "attractive-quadrant"},
                     "runtimes": {"copilot": {"models": ["gpt-test", "gpt-other"]}}})
        self.assertIn("no model ranking", self.z.pick_model("copilot")[2])
        entries = [{"model": "gpt-test", "intelligence": 90, "cost_per_task": 10},
                   {"model": "gpt-other", "intelligence": 70, "cost_per_task": 1}]
        with patch.object(self.z, "model_ranking",
                          return_value={"imported_at": "2026-10-26", "entries": entries}):
            self.assertEqual(self.z.pick_model("copilot")[0], "gpt-test")

    def test_invalid_ranking_is_not_imported(self):
        file = self.root / "ranking.json"
        with patch.object(self.z, "require_lock"):
            for bad in ("90", -1, float("nan"), True):
                with self.subTest(value=bad):
                    file.write_text(json.dumps({"entries": [
                        {"model": "test", "intelligence": bad, "cost_per_task": 1}]}))
                    with self.assertRaises(SystemExit):
                        self.z.cmd_models(["import", str(file)])
                    self.assertFalse(self.z.MODEL_RANKING_PATH.exists())

    def test_absent_copilot_does_not_break_ranking_status(self):
        self.config({"order": ["claude"], "model_policy": {"method": "attractive-quadrant"}})
        ranking = {"imported_at": "2026-10-26", "entries": [
            {"model": "claude-test-1.2", "intelligence": 80, "cost_per_task": 1}]}
        with patch.object(self.z.shutil, "which", return_value=None), \
                patch.object(self.z, "model_ranking", return_value=ranking):
            self.assertEqual(self.z.pick_model("copilot")[:2], (None, None))
            self.z.cmd_runtime(["status"])

    def test_corrupt_limit_state_does_not_enable_limited_runtime(self):
        self.z.RUNTIME_STATE_PATH.parent.mkdir(parents=True)
        self.z.RUNTIME_STATE_PATH.write_text("{broken")
        with self.assertRaises(SystemExit):
            self.z.runtime_state()
        self.assertIn("invalid JSON", self.output.getvalue())

    def test_launch_is_interactive_and_injects_worker_rules(self):
        self.config({"order": ["copilot"], "runtimes": {"copilot": {
            "permission_mode": "assisted", "deny_tools": ["shell(gh pr merge)"]}}})
        env, args = self.z.runtime_launch("copilot", "qa-agent", "gpt-test", "high")
        self.assertIn(f"COPILOT_CUSTOM_INSTRUCTIONS_DIRS={self.z.COPILOT_INSTRUCTIONS_DIR}", env)
        self.assertEqual(args[:2], ["--mode", "interactive"])
        self.assertNotIn("--autopilot", args)
        self.assertNotIn("--assisted-approval", args)
        self.assertNotIn("--allow-all-tools", args)
        self.assertNotIn("--max-ai-credits", args)
        self.assertIn("--deny-tool=shell(gh pr merge)", args)
        rules = self.z.GENERATED_WORKER_RULES.read_text()
        self.assertIn("not the coordinator", rules)
        self.assertNotIn("{{PROFILE}}", rules)
        self.assertEqual((self.z.COPILOT_INSTRUCTIONS_DIR / "zed-worker.instructions.md").read_text(), rules)
        _, args = self.z.runtime_launch("claude", "qa-agent", "opus", "high")
        self.assertIn("--append-system-prompt-file", args)
        self.assertIn("--effort", args)

    def test_permission_selection_verifies_footer_including_manual(self):
        menu = "Manual Approval\nAssisted Permissions\nAllow All"
        for mode, label in (("assisted", "Assisted Permissions"), ("manual", "Manual Approval"),
                            ("allow-all", "Allow All")):
            with self.subTest(mode=mode):
                self.config({"order": ["copilot"], "runtimes": {"copilot": {"permission_mode": mode}}})
                with patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
                    with patch.object(self.z, "read_screen", side_effect=[menu, f"prompt\n{label}"]):
                        self.assertIsNone(self.z.set_copilot_permissions("qa"))
                    self.assertIn(self.z.PERMISSION_MODE_MENU[mode], run.call_args.args[0])

    def test_permission_selection_accepts_a_loading_footer_once_the_menu_closed(self):
        menu = "Manual Approval\nAssisted Permissions\nAllow All"
        self.config({"order": ["copilot"], "runtimes": {"copilot": {"permission_mode": "assisted"}}})
        with patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            with patch.object(self.z, "read_screen", side_effect=[menu, "prompt\n ◉ Loading: 1 plugin — still waiting on extensions; ide"]):
                self.assertIsNone(self.z.set_copilot_permissions("qa"))
            with patch.object(self.z, "read_screen", side_effect=[menu] + [menu + "\nLoading:"] * 30):
                self.assertIn("could not switch", self.z.set_copilot_permissions("qa"))

    def test_permission_failure_does_not_look_successful(self):
        with patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 1, "", "blocked")):
            self.assertIn("could not open", self.z.set_copilot_permissions("qa"))

    def test_delayed_prompt_activity_does_not_resubmit_the_brief(self):
        for after in ({"agent_status": "working"},
                      {"agent_status": "done", "state_change_seq": 12}):
            with self.subTest(after=after):
                before = {"result": {"agent": {"state_change_seq": 10}}}
                with patch.object(self.z, "herdr_json", side_effect=[before, {"result": {"agent": after}}]), \
                        patch.object(self.z, "run", return_value=subprocess.CompletedProcess(
                            [], 1, "", "agent_prompt_stalled")) as run:
                    self.assertEqual(self.z.prompt_worker("qa", "brief"), (True, ""))
                    run.assert_called_once()

    def test_prompt_failure_is_reported(self):
        with patch.object(self.z, "herdr_json", return_value={"result": {"agent": {}}}), \
                patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 1, "", "agent_blocked")):
            self.assertEqual(self.z.prompt_worker("qa", "brief"), (False, "agent_blocked"))

    def test_limit_detection_and_credit_footer(self):
        for runtime, line in [
            ("claude", "You've hit your session limit - resets 3pm"),
            ("claude", "Usage limit reached - continuing automatically at 3pm"),
            ("copilot", "You've been rate limited on the current model"),
            ("copilot", "Quota exceeded for premium requests"),
            ("copilot", "Out of AI credits"),
        ]:
            self.assertIsNotNone(self.z.limit_line(runtime, line))
        self.assertIsNone(self.z.limit_line("copilot", "Added retry for HTTP 429"))
        self.assertIsNone(self.z.limit_line("claude", "Implemented an API rate limit"))
        conn = self.z.db()
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,runtime,created_at,updated_at) "
                     "VALUES (1,'r','main','feature/qa','/tmp/qa','qa','copilot','now','now')")
        session = conn.execute("SELECT * FROM sessions").fetchone()
        self.z.record_credits(conn, session, "Session: 4.71 AIC used")
        self.assertEqual(conn.execute("SELECT credits FROM sessions").fetchone()[0], 4.71)
        self.z.record_credits(conn, session, "footer unavailable")
        self.assertEqual(conn.execute("SELECT credits FROM sessions").fetchone()[0], 4.71)
        conn.execute("INSERT INTO tasks(id,title,status,created_at,updated_at) VALUES (1,'QA','done','now','now')")
        self.assertIn("4.71 Copilot AI credits", self.z.render_status(conn, live={}))

    def session(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO tasks(title,created_at,updated_at) VALUES ('QA','now','now')")
        conn.execute("INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,workspace_id,pane_id,runtime,"
                     "brief,created_at,updated_at) VALUES (1,'scratch','main','feature/qa',?,'qa','qa-ws','qa:p1',"
                     "'claude',?,'2000-01-01 00:00','2000-01-01 00:00')", (str(self.root), str(self.root / "profile.md")))
        conn.commit()
        return conn

    def test_handoff_keeps_exact_worktree_and_stops_old_workspace_first(self):
        conn = self.session()
        with patch.object(self.z, "db", return_value=conn), patch.object(self.z, "require_lock"), \
                patch.object(self.z, "project", return_value={}), \
                patch.object(self.z, "choose_runtime", return_value="copilot"), \
                patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 0, "feature/qa\n", "")) as run, \
                patch.object(self.z, "record_credits"), patch.object(self.z, "cmd_spawn", return_value=2) as spawn:
            (self.root / "briefs").mkdir()
            self.assertEqual(self.z.cmd_handoff(["S1", "runtime=auto"]), 2)
            self.assertEqual(spawn.call_args.kwargs["worktree"], self.root)
            self.assertTrue(any(c.args[0] == ["herdr", "workspace", "close", "qa-ws"] for c in run.call_args_list))
            note = (self.root / "briefs/T1-scratch-handoff-S1.md").read_text()
            self.assertIn("feature/qa", note)

    def test_handoff_refuses_to_spawn_if_old_workspace_cannot_close(self):
        conn = self.session()
        (self.root / "briefs").mkdir()

        def run(cmd, **kwargs):
            if cmd[:3] == ["herdr", "workspace", "close"]:
                return subprocess.CompletedProcess(cmd, 1, "", "server_unavailable")
            return subprocess.CompletedProcess(cmd, 0, "feature/qa\n", "")

        with patch.object(self.z, "db", return_value=conn), patch.object(self.z, "require_lock"), \
                patch.object(self.z, "project", return_value={}), \
                patch.object(self.z, "choose_runtime", return_value="copilot"), \
                patch.object(self.z, "run", side_effect=run), \
                patch.object(self.z, "record_credits"), patch.object(self.z, "cmd_spawn") as spawn:
            with self.assertRaises(SystemExit):
                self.z.cmd_handoff(["S1"])
            spawn.assert_not_called()
            self.assertEqual(conn.execute("SELECT status FROM sessions").fetchone()[0], "running")

    def test_closing_a_workspace_that_is_already_gone_succeeds(self):
        gone = subprocess.CompletedProcess([], 1, '{"error":{"code":"workspace_not_found"}}', "")
        with patch.object(self.z, "run", return_value=gone):
            self.z.close_workspace("w1")
        with patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 1, "", "server_unavailable")):
            with self.assertRaises(SystemExit):
                self.z.close_workspace("w1")

    def test_watch_reports_fast_turns_once_and_records_first_idle(self):
        self.session()
        agent = {"agent_status": "idle", "state_change_seq": 10}
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "live_agents", return_value={"qa:p1": agent}), \
                patch.object(self.z, "read_screen", return_value="ZED:QUESTION\nWhich greeting?") as screen:
            self.z.cmd_watch(["--once", "0.01"])
            self.assertIn("at watch start", self.output.getvalue())
            self.z.cmd_ack(["--all"])  # handled; an un-acked event would be shown again
            self.output.truncate(0)
            self.output.seek(0)
            with patch.object(self.z.time, "sleep", side_effect=InterruptedError):
                with self.assertRaises(InterruptedError):
                    self.z.cmd_watch(["--once", "0.01"])
            self.assertEqual(self.output.getvalue(), "")
            agent["state_change_seq"] = 12
            screen.return_value = "ZED:QUESTION\nWhich colour?"  # a repeated block would not be an event
            self.z.cmd_watch(["--once"])
            self.assertIn("qa", self.output.getvalue())

    def test_watch_exits_when_lock_is_lost(self):
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=False), \
                patch.object(self.z, "read_lock", return_value=None):
            with self.assertRaises(SystemExit) as error:
                self.z.cmd_watch(["--once"])
        self.assertEqual(error.exception.code, 3)
        self.assertIn("LOCK LOST", self.output.getvalue())

    DONE_SCREEN = """⏺ Done with the work.

  ZED:DONE
  Summary: Added the digest label
  Branch: story/ment-1-x  Commits: abc123
  PR: https://github.com/org/repo/pull/7  (draft)
  QA: playwright-cli on localhost, happy path and empty digest
  Follow-ups: none
  Learnings: run npm ci first
    then npm test
────────────────────────
❯ 
  ⏵⏵ accept edits on"""

    def test_parse_block_kinds_and_chrome(self):
        b = self.z.parse_block(self.DONE_SCREEN)
        self.assertEqual(b["kind"], "DONE")
        self.assertEqual(b["fields"]["PR"], "https://github.com/org/repo/pull/7  (draft)")
        self.assertEqual(b["fields"]["Learnings"], "run npm ci first\nthen npm test")
        self.assertNotIn("accept edits", b["text"])
        q = self.z.parse_block("ZED:QUESTION\n1. Which greeting?\n   Hello or Hi.\n2. Which colour?\n```\n")
        self.assertEqual(q["items"], ["Which greeting?\nHello or Hi.", "Which colour?"])
        self.assertEqual(self.z.parse_block("ZED:BLOCKED\nNeed credentials")["items"], ["Need credentials"])
        self.assertIsNone(self.z.parse_block("no marker here, just prose mentioning ZED:DONE inline"))
        self.assertIsNone(self.z.parse_block("ZED:DONE\n" + "line\n" * 100))
        last = self.z.parse_block("ZED:QUESTION\n1. old\n\nZED:DONE\nSummary: s\nQA: q")
        self.assertEqual(last["kind"], "DONE")

    def test_record_block_done_sets_pr_status_and_flags_missing_qa(self):
        conn = self.session()
        conn.execute("UPDATE tasks SET status='active'")
        s = conn.execute("SELECT * FROM sessions").fetchone()
        text = self.z.record_block(conn, s, self.z.parse_block(self.DONE_SCREEN))
        self.assertIn("PR: https://github.com/org/repo/pull/7", text)
        self.assertNotIn("⚠", text)
        row = conn.execute("SELECT s.pr_url, s.status, t.status FROM sessions s JOIN tasks t ON t.id=s.task_id").fetchone()
        self.assertEqual(tuple(row), ("https://github.com/org/repo/pull/7", "finished", "pr_open"))
        conn.execute("UPDATE tasks SET status='active'")
        noqa = self.z.parse_block("ZED:DONE\nSummary: s2\nPR: https://github.com/o/r/pull/8\nQA: none")
        self.assertIn("⚠", self.z.record_block(conn, s, noqa))
        self.assertEqual(conn.execute("SELECT status FROM tasks").fetchone()[0], "active")

    def test_record_block_questions_are_recorded_once(self):
        conn = self.session()
        s = conn.execute("SELECT * FROM sessions").fetchone()
        block = self.z.parse_block("ZED:QUESTION\n1. Which greeting?\n2. Which colour?")
        self.assertIn("Q1: Which greeting?", self.z.record_block(conn, s, block))
        again = self.z.record_block(conn, s, block)
        self.assertIn("already recorded", again)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT status FROM sessions").fetchone()[0], "waiting_user")

    def settle(self, screen, alerts, **kw):
        conn = self.session() if not hasattr(self, "_conn") else self._conn
        self._conn = conn
        s = conn.execute("SELECT * FROM sessions").fetchone()
        with patch.object(self.z, "read_screen", return_value=screen):
            return self.z.settle_turn(conn, s, screen, alerts, "S1 T1 qa → idle", 5), s

    def test_settle_turn_surfaces_a_new_block_once_then_treats_it_as_a_lapse(self):
        alerts = {}
        line, s = self.settle(self.DONE_SCREEN, alerts)
        self.assertIn("ZED:DONE", line)
        line, _ = self.settle(self.DONE_SCREEN, alerts)  # same block, still idle: nothing new
        self.assertIsNone(line)
        self.assertIn("lapse", alerts[s["id"]])

    def test_limit_line_marks_runtime_limited_and_wakes_once(self):
        self.config({"order": ["claude"]})
        alerts = {}
        screen = "working...\nYou've hit your session limit - resets 3pm"
        line, s = self.settle(screen, alerts)
        self.assertIn("claude marked limited", line)
        self.assertIn("claude", json.loads(self.z.RUNTIME_STATE_PATH.read_text()))
        again, _ = self.settle(screen, alerts)
        self.assertIsNone(again)

    def test_protocol_lapse_nudges_once_and_wakes_on_repeat(self):
        conn = self.session()
        s = conn.execute("SELECT * FROM sessions").fetchone()
        alerts = {1: {"lapse": {"seq": 5, "since": 0}}}
        with patch.object(self.z, "run") as run:
            self.assertEqual(self.z.lapse_tick(s, "idle", 5, alerts)[0], "nudge")
            self.assertIn("ZED:QUESTION", run.call_args.args[0][-1])
            self.assertIsNone(self.z.lapse_tick(s, "idle", 5, alerts))  # lapse consumed
            alerts[1]["lapse"] = {"seq": 6, "since": 0}
            self.assertEqual(self.z.lapse_tick(s, "idle", 6, alerts)[0], "wake")
            run.assert_called_once()  # no second nudge
        alerts[1]["lapse"] = {"seq": 7, "since": 0}
        self.assertIsNone(self.z.lapse_tick(s, "working", 7, alerts))  # woke up on its own
        alerts[1]["lapse"] = {"seq": 7, "since": 10 ** 12}
        self.assertIsNone(self.z.lapse_tick(s, "idle", 7, alerts))  # still inside the grace period

    def test_watch_records_the_block_and_puts_it_in_the_event(self):
        conn = self.session()
        agent = {"agent_status": "idle", "state_change_seq": 10}
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "live_agents", return_value={"qa:p1": agent}), \
                patch.object(self.z, "read_screen", return_value=self.DONE_SCREEN):
            self.z.cmd_watch(["--once", "0.01"])
        out = self.output.getvalue()
        self.assertIn("ZED:DONE · Added the digest label", out)
        self.assertEqual(conn.execute("SELECT pr_url FROM sessions").fetchone()[0], "https://github.com/org/repo/pull/7")

    def test_brief_status_is_bounded_and_start_prints_a_digest(self):
        conn = self.session()
        for i in range(20):
            conn.execute("INSERT INTO questions(task_id,session_id,question,asked_at) VALUES (1,1,?,'now')", (f"q{i}",))
        conn.commit()
        brief = "\n".join(self.z.render_brief(conn, live={"qa:p1": {"agent_status": "idle"}}))
        self.assertIn("8 more omitted", brief)
        self.assertIn("S1 T1 qa claude running/idle", brief)
        with patch.object(self.z, "cmd_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "live_agents", return_value={}), patch.object(self.z, "watcher_alive", return_value=None):
            self.output.truncate(0)
            self.z.cmd_start([])
        self.assertIn("watcher: NOT RUNNING", self.output.getvalue())

    def test_second_watcher_is_refused(self):
        with patch.object(self.z, "require_lock"), patch.object(self.z, "claim_watcher", return_value=False), \
                patch.object(self.z, "watcher_alive", return_value=999):
            with self.assertRaises(SystemExit) as e:
                self.z.cmd_watch(["--once"])
        self.assertEqual(e.exception.code, 4)
        self.assertIn("WATCH_ALREADY_RUNNING", self.output.getvalue())

    def test_brief_scaffold_and_spawn_refuses_placeholders(self):
        conn = self.session()
        conn.execute("UPDATE tasks SET jira='MENT-9', description='Add a label'")
        conn.commit()
        (self.root / "config").mkdir(exist_ok=True)
        (self.root / "config" / "projects.json").write_text(json.dumps({
            "worktree_root": str(self.root / "wt"), "repos": {"r": {"path": str(self.root / "r"), "role": "backend"}}}))
        notes = self.root / "knowledge" / "repos"
        notes.mkdir(parents=True)
        (notes / "r.md").write_text("# r\n## Setup\nnpm ci\n## History\nold stuff\n## Test logins\nadmin / from vault\n")
        self.z.cmd_brief(["T1", "repo=r", "base=main", "branch=story/ment-9-label"])
        brief = (self.root / "briefs" / "T1-r.md").read_text()
        self.assertIn("MENT-9", brief)
        self.assertIn("admin / from vault", brief)
        self.assertNotIn("old stuff", brief)
        self.assertNotIn("{{", brief)
        self.assertIn("Add a label", brief)
        with patch.object(self.z, "require_lock"), patch.object(self.z, "choose_runtime", return_value="claude"):
            with self.assertRaises(SystemExit):
                self.z.cmd_brief(["T1", "repo=r", "base=main", "branch=b"])  # exists, no force
            with self.assertRaises(SystemExit):
                self.z.cmd_spawn(["T1", "repo=r", "base=main", "branch=b", f"brief={self.root / 'briefs' / 'T1-r.md'}"])
        self.assertIn("unfilled placeholder", self.output.getvalue())

    def test_effort_by_kind_is_capped_and_overridable(self):
        policy = {"method": "default", "max_effort": "high",
                  "effort_by_kind": {"story": "medium", "investigation": "high", "design": "max"}}
        self.config({"order": ["claude"], "model_policy": policy})
        self.assertEqual(self.z.effort_for_kind("claude", "story", "high", "w")[0], "medium")
        self.assertEqual(self.z.effort_for_kind("claude", "investigation", "low", "w")[0], "high")
        self.assertEqual(self.z.effort_for_kind("claude", "design", None, "w")[0], "high")  # capped by max_effort
        self.assertEqual(self.z.effort_for_kind("claude", "unmapped", "low", "w")[0], "low")
        self.assertEqual(self.z.effort_for_kind("claude", None, "low", "w")[0], "low")
        # the ceiling still applies on top of the kind's effort
        self.config({"order": ["claude"], "model_policy": policy,
                     "runtimes": {"claude": {"ceiling": {"model": "m", "effort": "medium"}}}})
        effort = self.z.effort_for_kind("claude", "investigation", None, "w")[0]
        self.assertEqual(self.z.apply_ceiling("claude", "m", effort, "w")[1], "medium")

    def test_max_running_counts_every_open_session_of_the_runtime(self):
        conn = self.session()
        self.config({"order": ["claude", "copilot"], "runtimes": {"claude": {"max_running": 3}}})
        live = {"w1:p1": {"agent": "claude"}, "w2:p1": {"agent": "claude"},  # e.g. Zed itself and a worker
                "w3:p1": {"agent": "copilot"}}
        with patch.object(self.z, "live_agents", return_value=live):
            self.assertEqual(self.z.running_count(conn, "claude"), 2)
            self.z.require_capacity(conn, "claude", 2)
            live["w4:p1"] = {"agent": "claude"}  # the user opens their own session
            with self.assertRaises(SystemExit) as e:
                self.z.require_capacity(conn, "claude", 2)
            self.assertEqual(e.exception.code, 75)
            self.assertIn("queued: 3 claude sessions are open", self.output.getvalue())
            self.z.require_capacity(conn, "copilot", 2)  # no cap configured
        with patch.object(self.z, "live_agents", return_value={}):  # herdr unavailable: the DB's own sessions
            self.assertEqual(self.z.running_count(conn, "claude"), 1)
            conn.execute("UPDATE sessions SET status='closed'")
            self.assertEqual(self.z.running_count(conn, "claude"), 0)

    def test_coordinator_command_per_mode_within_the_ceiling(self):
        self.config({"order": ["claude"], "coordinator": {"routine": {"model": "cheap", "effort": "low"},
                                                          "judgment": {"model": "top", "effort": "max"}},
                     "runtimes": {"claude": {"ceiling": {"model": "top", "effort": "high"}}}})
        self.z.MODEL_RANKING_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.MODEL_RANKING_PATH.write_text(json.dumps({"entries": [
            {"model": "cheap", "intelligence": 1, "cost_per_task": 1}, {"model": "top", "intelligence": 2, "cost_per_task": 5}]}))
        self.assertEqual(self.z.coordinator_command("routine"), ["claude", "--model", "cheap", "--effort", "low"])
        self.assertEqual(self.z.coordinator_command("judgment"), ["claude", "--model", "top", "--effort", "high"])
        with self.assertRaises(SystemExit):
            self.z.coordinator_command("other")
        self.z.cmd_coordinator(["--print"])
        self.assertIn("/model cheap", self.output.getvalue())

    def test_quota_sums_window_tokens_across_sessions_and_warns_past_the_threshold(self):
        import time as real_time
        from datetime import datetime, timedelta, timezone
        root = self.root / "projects"
        stamp = lambda hours: (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        def row(mid, hours, out, inp=0):
            return json.dumps({"timestamp": stamp(hours), "requestId": "r" + mid,
                               "message": {"id": mid, "usage": {"output_tokens": out, "input_tokens": inp}}})
        for name, rows in {"-a": [row("1", 1, 100, 10), row("1", 1, 100, 10), row("2", 9, 5000)],
                           "-b": [row("3", 2, 200)]}.items():
            (root / name).mkdir(parents=True)
            (root / name / "s.jsonl").write_text("\n".join(rows) + "\n")
        total, by_project = self.z.window_usage(5, root)
        self.assertEqual((total["output"], total["input"]), (300, 10))  # deduplicated, 9h-old row excluded
        self.assertEqual(by_project, {"-a": 100, "-b": 200})
        self.config({"order": ["claude"], "quota": {"window_hours": 5, "threshold_tokens": 300}})
        with patch.object(self.z, "window_usage", return_value=(total, by_project)):
            self.assertIn("past quota.threshold_tokens", self.z.quota_warning())
            self.config({"order": ["claude"], "quota": {"threshold_tokens": 10 ** 9}})
            self.assertIsNone(self.z.quota_warning())
            self.config({"order": ["claude"]})
            self.assertIsNone(self.z.quota_warning())  # no threshold configured: never warns
            self.z.cmd_quota([])
        self.assertIn("no quota.threshold_tokens set", self.output.getvalue())

    def test_close_appends_to_the_spend_ledger_and_status_shows_spend_against_plan(self):
        conn = self.session()
        s = conn.execute("SELECT * FROM sessions").fetchone()
        (self.root / "briefs").mkdir(exist_ok=True)
        (self.root / "briefs" / "T1-scratch-plan.md").write_text("- [x] a\n- [x] b\n- [ ] c\n")
        usage = {"output": 400_000, "input": 10, "cache_read": 5, "cache_write": 7}
        with patch.object(self.z, "claude_usage", return_value=usage):
            self.z.record_spend(conn, s)
            rows = self.z.spend_rows()
            self.assertEqual((rows[0]["session"], rows[0]["plan_done"], rows[0]["plan_total"]), (1, 2, 3))
            self.assertEqual(rows[0]["tokens"]["output"], 400_000)
            status = self.z.render_status(conn, live={})
        self.assertIn("spend (closed sessions): 0.40M output tokens", status)
        self.assertIn("plan 2/3 steps", status)
        self.assertIn("200k output tokens per step", status)
        conn.execute("UPDATE sessions SET external=1")
        self.z.record_spend(conn, conn.execute("SELECT * FROM sessions").fetchone())
        self.assertEqual(len(self.z.spend_rows()), 1)  # external sessions aren't ours to account

    def hook(self, mode, data=None, lock=True, tracked=True, watcher=None, watch=None):
        """Run `zed hook <mode>`; returns the exit code (0 when it simply returns)."""
        with patch.object(self.z, "hook_input", return_value=data or {}), \
                patch.object(self.z, "holds_lock", return_value=lock), \
                patch.object(self.z, "has_tracked_work", return_value=tracked), \
                patch.object(self.z, "watcher_alive", return_value=watcher), \
                patch.object(self.z, "cmd_watch", side_effect=watch or (lambda args: False)) as cmd_watch, \
                patch.object(self.z, "GUARD_WAIT", 0), patch.object(self.z, "ARM_MIN_SECONDS", 0):
            try:
                self.z.cmd_hook([mode])
            except SystemExit as e:
                return e.code, cmd_watch
            return 0, cmd_watch

    def test_stop_guard_blocks_a_blind_turn_end_once(self):
        self.assertEqual(self.hook("stop-guard")[0], 2)
        self.assertIn("no watcher is running", self.output.getvalue())
        self.assertEqual(self.hook("stop-guard", data={"stop_hook_active": True})[0], 0)  # never loops
        self.assertEqual(self.hook("stop-guard", watcher=123)[0], 0)
        self.z.ARM_STAMP_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.ARM_STAMP_PATH.touch()  # arm just ran (and woke Zed with an event): nothing to block
        self.assertEqual(self.hook("stop-guard")[0], 0)
        os.utime(self.z.ARM_STAMP_PATH, (1, 1))  # an old stamp doesn't count
        self.assertEqual(self.hook("stop-guard")[0], 2)
        self.assertEqual(self.hook("stop-guard", lock=False)[0], 0)  # read-only sessions are untouched
        self.assertEqual(self.hook("stop-guard", tracked=False)[0], 0)

    def test_arm_hook_wakes_only_on_events_and_only_when_it_may(self):
        code, cmd_watch = self.hook("arm", watch=lambda args: True)
        self.assertEqual(code, 2)
        self.assertEqual(cmd_watch.call_args.args[0], ["--once", "--exit-when-idle"])
        self.assertEqual(self.hook("arm", watch=lambda args: False)[0], 0)  # went idle: no wake
        self.assertEqual(self.hook("arm", watch=lambda args: sys.exit(3))[0], 2)  # lock lost: tell Zed
        self.assertEqual(self.hook("arm", watch=lambda args: sys.exit(4))[0], 0)  # lost the race to another watcher
        for kw in ({"lock": False}, {"tracked": False}, {"watcher": 123}):
            code, cmd_watch = self.hook("arm", **kw)
            self.assertEqual(code, 0)
            cmd_watch.assert_not_called()

    def test_broken_hook_never_blocks(self):
        code, _ = self.hook("arm", watch=lambda args: 1 / 0)
        self.assertEqual(code, 1)

    def test_one_watcher_at_a_time(self):
        self.assertIsNone(self.z.watcher_alive())
        self.assertTrue(self.z.claim_watcher())
        self.addCleanup(self.z._watcher_lock.close)
        self.assertEqual(self.z.watcher_alive(), os.getpid())
        self.assertTrue(self.z.claim_watcher())  # idempotent in the watcher itself
        other = subprocess.run([sys.executable, "-c", (
            "import fcntl,sys; f=open(sys.argv[1],'a')\n"
            "try: fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
            "except BlockingIOError: sys.exit(7)"), str(self.z.WATCHER_LOCK_PATH)])
        self.assertEqual(other.returncode, 7)  # another process can't claim it

    def test_block_printed_while_still_working_is_reported_once(self):
        """S19 printed a ZED:QUESTION and stayed 'working' (waiting on a background workflow)."""
        conn = self.session()
        s = conn.execute("SELECT * FROM sessions").fetchone()
        alerts = {}
        question = "ZED:QUESTION\n1. Which greeting?"
        with patch.object(self.z, "WORKING_SCAN", 0), patch.object(self.z, "read_screen") as screen:
            screen.return_value = "ZED:QUESTION\n1. Which gr"  # half printed
            self.assertIsNone(self.z.scan_working(conn, s, alerts))
            screen.return_value = question
            self.assertIsNone(self.z.scan_working(conn, s, alerts))  # changed since last look: wait for it to settle
            line = self.z.scan_working(conn, s, alerts)
            self.assertIn("new ZED:QUESTION while still working", line)
            self.assertIn("Q1: Which greeting?", line)
            self.assertIsNone(self.z.scan_working(conn, s, alerts))  # same block: not reported again
            self.assertIsNone(self.z.scan_working(conn, s, alerts))
            screen.return_value = "just working output"
            self.assertIsNone(self.z.scan_working(conn, s, alerts))
        with patch.object(self.z, "read_screen", return_value="ZED:QUESTION\n1. Another?"):
            self.assertIsNone(self.z.scan_working(conn, s, alerts))  # throttled by WORKING_SCAN
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 1)

    def test_watch_reports_a_block_from_a_working_worker(self):
        conn = self.session()
        conn.execute("UPDATE sessions SET created_at=?", (self.z.now(),))  # recent: no STALL event
        conn.commit()
        agent = {"agent_status": "working", "state_change_seq": 3}
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "claim_watcher", return_value=True), patch.object(self.z, "WORKING_SCAN", 0), \
                patch.object(self.z, "live_agents", return_value={"qa:p1": agent}), \
                patch.object(self.z, "read_screen", return_value="ZED:BLOCKED\nNeed a login"), \
                patch.object(self.z.time, "sleep"):
            self.z.cmd_watch(["--once", "0.01"])
        self.assertIn("new ZED:BLOCKED while still working", self.output.getvalue())
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 1)

    def test_status_file_is_read_in_any_state_and_not_reported_twice(self):
        conn = self.session()
        conn.execute("UPDATE sessions SET created_at=?", (self.z.now(),))
        conn.commit()
        s = conn.execute("SELECT * FROM sessions").fetchone()
        self.z.STATUS_DIR.mkdir(parents=True)
        path = self.z.STATUS_DIR / "S1.log"
        path.write_text("needs-decision: Which greeting?\nneeds-decision: Which colour?\ndone: half writ")
        alerts = {}
        lines = self.z.new_status_lines(s, alerts)
        self.assertEqual(lines, ["needs-decision: Which greeting?", "needs-decision: Which colour?"])  # no newline yet
        blocks = self.z.status_blocks(lines)
        self.assertEqual((blocks[0]["kind"], blocks[0]["items"]), ("QUESTION", ["Which greeting?", "Which colour?"]))
        self.z.record_block(conn, s, blocks[0])
        path.write_text(path.read_text() + "ten\nblocked: Need a login\n")
        more = self.z.status_blocks(self.z.new_status_lines(s, alerts))
        self.assertEqual([b["kind"] for b in more], ["DONE", "BLOCKED"])
        self.assertEqual(self.z.new_status_lines(s, alerts), [])
        done = self.z.status_blocks(["done: Added label; pr=https://github.com/o/r/pull/3; qa=playwright, happy path"])[0]
        self.assertEqual(done["fields"], {"Summary": "Added label", "PR": "https://github.com/o/r/pull/3",
                                          "QA": "playwright, happy path"})
        # the same question later printed as a ZED block is already known: no second event
        self.assertTrue(self.z.block_known(conn, s, self.z.parse_block("ZED:QUESTION\n1. Which greeting?")))
        conn.commit()
        self._conn = conn
        line, _ = self.settle("ZED:QUESTION\n1. Which greeting?", {})
        self.assertIsNone(line)

    def test_watch_reports_status_file_lines_while_working(self):
        conn = self.session()
        conn.execute("UPDATE sessions SET created_at=?", (self.z.now(),))
        conn.commit()
        self.z.STATUS_DIR.mkdir(parents=True)
        (self.z.STATUS_DIR / "S1.log").write_text("needs-decision: Which greeting?\n")
        agent = {"agent_status": "working", "state_change_seq": 3}
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "claim_watcher", return_value=True), \
                patch.object(self.z, "live_agents", return_value={"qa:p1": agent}), \
                patch.object(self.z, "read_screen", return_value="busy"):
            self.z.cmd_watch(["--once", "0.01"])
        self.assertIn("status file: ZED:QUESTION", self.output.getvalue())
        self.assertIn("Q1: Which greeting?", self.output.getvalue())

    def test_wake_queue_keeps_events_until_acked_and_never_loops_forever(self):
        self.assertEqual(self.z.queue_add(["S1 finished", "S2 asks"]), ["[E1] S1 finished", "[E2] S2 asks"])
        with patch.object(self.z, "require_lock"):
            self.assertEqual(len(self.z.queue_represent()), 2)  # shown a 2nd time (a crash lost the 1st)
            self.assertEqual(len(self.z.queue_represent()), 2)  # a 3rd
            self.assertEqual(self.z.queue_represent(), [])  # capped: a forgotten ack can't loop the hook
            self.assertEqual(len(self.z.queue_pending()), 2)  # but `start` still lists them
            self.z.cmd_ack(["E1"])
            self.assertEqual([e["id"] for e in self.z.queue_pending()], [2])
            self.z.cmd_ack(["--all"])
            self.assertEqual(self.z.queue_pending(), [])
            with self.assertRaises(SystemExit):
                self.z.cmd_ack(["E99"])

    def test_watch_represents_unacked_events_before_anything_else(self):
        self.session()
        self.z.queue_add(["S1 T1 qa asked something"])
        with patch.object(self.z, "require_lock"), patch.object(self.z, "claim_watcher", return_value=True), \
                patch.object(self.z, "live_agents", side_effect=AssertionError("must not poll")):
            self.assertTrue(self.z.cmd_watch(["--once"]))
        self.assertIn("[E1] (again, un-acked", self.output.getvalue())
        self.assertIn("asked something", self.output.getvalue())

    def test_watch_events_are_queued_with_ids(self):
        conn = self.session()
        agent = {"agent_status": "idle", "state_change_seq": 10}
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "claim_watcher", return_value=True), \
                patch.object(self.z, "live_agents", return_value={"qa:p1": agent}), \
                patch.object(self.z, "read_screen", return_value=self.DONE_SCREEN):
            self.z.cmd_watch(["--once", "0.01"])
        self.assertRegex(self.output.getvalue(), r"\[E1\] S1 T1 qa is idle")
        self.assertEqual(len(self.z.queue_pending()), 1)

    def test_context_budget_check(self):
        self.z.ZED_HOME = self.root / "home"
        self.z.ZED_HOME.mkdir()
        (self.z.ZED_HOME / "CLAUDE.md").write_text("word " * 60)
        (self.root / "lessons.md").write_text("word " * 30)
        words, budget = self.z.context_words()
        self.assertEqual((words["CLAUDE.md"], words["lessons.md"], budget), (60, 30, 9000))
        self.assertIn("94 of 9000 words", self.z.context_line())
        (self.root / "config").mkdir(exist_ok=True)
        (self.root / "config" / "settings.json").write_text('{"context_budget_words": 90}')
        self.assertIn("OVER BUDGET", self.z.context_line())
        with self.assertRaises(SystemExit) as e:
            self.z.cmd_doctor(["--quiet"])
        self.assertEqual(e.exception.code, 1)
        (self.root / "config" / "settings.json").write_text('{"context_budget_words": 94}')
        with self.assertRaises(SystemExit) as e:
            self.z.cmd_doctor(["--quiet"])
        self.assertEqual(e.exception.code, 0)

    def git_worktree(self):
        """A throwaway clone with an upstream, clean and pushed."""
        remote, work = self.root / "remote.git", self.root / "work"
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for cmd in (["git", "init", "-q", "--bare", str(remote)], ["git", "init", "-q", "-b", "feature/qa", str(work)]):
            subprocess.run(cmd, check=True, env=env)
        (work / "a.txt").write_text("a")
        for cmd in (["add", "."], ["commit", "-qm", "a"], ["remote", "add", "origin", str(remote)], ["push", "-qu", "origin", "feature/qa"]):
            subprocess.run(["git", "-C", str(work), *cmd], check=True, env=env)
        return work, env

    def test_landed_work_test_lists_exactly_what_blocks_closing(self):
        conn = self.session()
        work, env = self.git_worktree()
        conn.execute("UPDATE sessions SET worktree=?", (str(work),))
        conn.commit()
        s = lambda: conn.execute("SELECT * FROM sessions").fetchone()
        self.assertEqual(self.z.landed_blockers(conn, s()), [])
        (work / "b.txt").write_text("b")
        (work / "a.txt").write_text("changed")
        subprocess.run(["git", "-C", str(work), "commit", "-qam", "local"], check=True, env=env)
        (work / "c.txt").write_text("c")
        conn.execute("UPDATE sessions SET pr_url='https://github.com/o/r/pull/1'")
        conn.execute("INSERT INTO questions(task_id,session_id,question,asked_at) VALUES (1,1,'q','now')")
        (self.root / "briefs").mkdir(exist_ok=True)
        (self.root / "briefs" / "T1-scratch-plan.md").write_text("- [x] a\n- [ ] b\n")
        with patch.object(self.z, "pr_snapshot", return_value={"state": "open", "reviews": 0, "checks": None}):
            blockers = self.z.landed_blockers(conn, s())
        text = "\n".join(blockers)
        for part in ("2 uncommitted file(s): b.txt, c.txt", "1 commit(s) not pushed", "PR still open", "plan has 1 of 2 steps left",
                     "1 open question(s)"):
            self.assertIn(part, text)
        with patch.object(self.z, "pr_snapshot", return_value={"state": "merged", "reviews": 0, "checks": None}):
            self.assertNotIn("PR still open", "\n".join(self.z.landed_blockers(conn, s())))
        with patch.object(self.z, "pr_snapshot", return_value=None):
            self.assertIn("PR state unknown", "\n".join(self.z.landed_blockers(conn, s())))

    def test_unpushed_count_ignores_an_upstream_that_is_the_base_branch(self):
        conn = self.session()
        work, env = self.git_worktree()
        conn.execute("UPDATE sessions SET worktree=?", (str(work),))
        conn.commit()
        subprocess.run(["git", "-C", str(work), "branch", "-u", "origin/feature/qa"], check=True, env=env)
        s = conn.execute("SELECT * FROM sessions").fetchone()
        self.assertEqual(self.z.landed_blockers(conn, s), [])
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", "feature/unpushed"], check=True, env=env)
        (work / "n.txt").write_text("n")
        subprocess.run(["git", "-C", str(work), "add", "."], check=True, env=env)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "n"], check=True, env=env)
        subprocess.run(["git", "-C", str(work), "branch", "-u", "origin/feature/qa"], check=True, env=env)  # upstream = another branch
        self.assertIn("1 commit(s) not pushed", "\n".join(self.z.landed_blockers(conn, s)))

    def test_work_merged_into_the_main_checkouts_branch_is_not_unpushed(self):
        conn = self.session()
        main, env = self.git_worktree()  # the repo's own folder, on a branch with an upstream
        wt = self.root / "wt"
        git = lambda path, *a: subprocess.run(["git", "-C", str(path), *a], check=True, env=env, capture_output=True)
        git(main, "worktree", "add", "-q", "-b", "feature/done", str(wt))
        (wt / "n.txt").write_text("n")
        git(wt, "add", ".")
        git(wt, "commit", "-qm", "n")
        conn.execute("UPDATE sessions SET worktree=?", (str(wt),))
        conn.commit()
        s = conn.execute("SELECT * FROM sessions").fetchone()
        (self.root / "config").mkdir(exist_ok=True)
        (self.root / "config" / "projects.json").write_text(json.dumps({"repos": {"scratch": {"path": str(main)}}}))
        self.assertEqual(self.z.main_checkout_heads("scratch", wt), ["refs/heads/feature/qa"])
        self.assertIn("1 commit(s) not pushed", "\n".join(self.z.landed_blockers(conn, s)))
        git(main, "merge", "-q", "feature/done")  # merged into the local-only branch: nothing is lost by removing wt
        self.assertEqual(self.z.landed_blockers(conn, s), [])
        self.assertEqual(self.z.main_checkout_heads("unknown", wt), [])

    def test_close_refuses_unlanded_worktree_but_keep_worktree_closes_the_session(self):
        conn = self.session()
        work, _ = self.git_worktree()
        (work / "dirty.txt").write_text("x")
        conn.execute("UPDATE sessions SET worktree=?", (str(work),))
        conn.commit()
        conn.close()
        with patch.object(self.z, "require_lock"), patch.object(self.z, "project", return_value={}), \
                patch.object(self.z, "close_workspace") as close_ws:
            with self.assertRaises(SystemExit):
                self.z.cmd_close(["1"])
            self.assertIn("not landed", self.output.getvalue())
            self.assertIn("dirty.txt", self.output.getvalue())
            close_ws.assert_not_called()
            self.z.cmd_close(["1", "keep-worktree"])
        self.assertIn("note: S1 is not landed yet", self.output.getvalue())
        close_ws.assert_called_once()
        self.assertTrue(work.exists())

    def test_pr_snapshot_for_github_and_bitbucket(self):
        gh = json.dumps({"state": "OPEN", "reviews": [{}], "comments": [{}, {}],
                         "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]})
        with patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 0, gh, "")):
            self.assertEqual(self.z.pr_snapshot("https://github.com/o/r/pull/1"),
                             {"state": "open", "reviews": 3, "checks": "failing"})
        bb = json.dumps({"state": "MERGED", "comment_count": 2, "approvals": 1, "build_failed": False})
        with patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 0, bb, "")) as run:
            self.assertEqual(self.z.pr_snapshot("https://bitbucket.org/ws/repo/pull-requests/7"),
                             {"state": "merged", "reviews": 3, "checks": None})
            self.assertEqual(run.call_args.args[0][-4:], ["view", "7", "--repo", "ws/repo"])
        with patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 1, "", "no")):
            self.assertIsNone(self.z.pr_snapshot("https://github.com/o/r/pull/1"))
        self.assertIsNone(self.z.pr_snapshot("https://example.com/x"))

    def test_pr_polling_reports_merge_new_reviews_and_failing_checks_only(self):
        conn = self.session()
        conn.execute("UPDATE sessions SET pr_url='https://github.com/o/r/pull/1', status='finished'")
        conn.commit()
        s = lambda: conn.execute("SELECT * FROM sessions").fetchone()
        alerts = {}
        snaps = iter([{"state": "open", "reviews": 1, "checks": "pending"},    # baseline: silent
                      {"state": "open", "reviews": 1, "checks": "pending"},    # unchanged: silent
                      {"state": "open", "reviews": 3, "checks": "failing"},    # 2 new + failing
                      {"state": "open", "reviews": 3, "checks": "failing"},    # same failure: silent
                      {"state": "merged", "reviews": 3, "checks": "passing"}])
        with patch.object(self.z, "PR_POLL_SECONDS", 0), patch.object(self.z, "pr_snapshot", side_effect=lambda url: next(snaps)):
            self.assertEqual(self.z.pr_events(conn, s(), alerts), [])
            self.assertEqual(self.z.pr_events(conn, s(), alerts), [])
            lines = self.z.pr_events(conn, s(), alerts)
            self.assertIn("2 new review(s)/comment(s)", lines[0])
            self.assertIn("failing checks", lines[1])
            self.assertEqual(self.z.pr_events(conn, s(), alerts), [])
            merged = self.z.pr_events(conn, s(), alerts)
            self.assertIn("was merged", merged[0])
        self.assertEqual(conn.execute("SELECT pr_state FROM sessions").fetchone()[0], "merged")
        self.assertIsNone(conn.execute(self.z.OPEN_PRS).fetchone())  # no longer polled or tracked
        with patch.object(self.z, "PR_POLL_SECONDS", 300), patch.object(self.z, "pr_snapshot", side_effect=AssertionError):
            self.assertEqual(self.z.pr_events(conn, s(), {1: {"pr_at": self.z.time.time()}}), [])  # slow interval

    def test_watch_polls_prs_of_finished_sessions_and_has_tracked_work(self):
        conn = self.session()
        conn.execute("UPDATE sessions SET pr_url='https://github.com/o/r/pull/1', status='finished'")
        conn.commit()
        self.assertTrue(self.z.has_tracked_work())
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "claim_watcher", return_value=True), patch.object(self.z, "PR_POLL_SECONDS", 0), \
                patch.object(self.z, "live_agents", return_value={}), \
                patch.object(self.z, "pr_snapshot", return_value={"state": "closed", "reviews": 0, "checks": None}):
            self.assertTrue(self.z.cmd_watch(["--once", "0.01"]))
        self.assertIn("was closed", self.output.getvalue())
        self.assertFalse(self.z.has_tracked_work())

    def test_context_size_is_the_last_main_thread_turn_and_flags_once(self):
        home = self.root / "home"
        wt = self.root / "wt"
        sid = "abc"
        f = home / ".claude" / "projects" / self.z.re.sub(r"[^A-Za-z0-9]", "-", str(wt)) / f"{sid}.jsonl"
        f.parent.mkdir(parents=True)
        turn = lambda i, c, side=False: json.dumps({"isSidechain": side, "message": {"id": i, "usage": {
            "input_tokens": 10, "cache_read_input_tokens": c, "cache_creation_input_tokens": 5, "output_tokens": 1}}})
        f.write_text("\n".join([turn("1", 1000), turn("2", 200_000), turn("3", 999_999, side=True), "not json"]) + "\n")
        with patch.object(self.z.Path, "home", return_value=home):
            self.assertEqual(self.z.claude_context(str(wt), sid), 200_015)  # not the sub-agent's turn
            conn = self.session()
            s = {"id": 1, "runtime": "claude", "worktree": str(wt), "claude_session_id": sid}
            alerts = {}
            with patch.object(self.z, "CONTEXT_CHECK_SECONDS", 0):
                self.assertEqual(self.z.context_check(s, None, alerts), 200_015)
                self.assertIsNone(self.z.context_check(s, None, alerts))  # flagged once
            self.assertIn("restart it fresh", self.z.context_note(s, alerts))
            self.config({"order": ["claude"], "runtimes": {"claude": {"context_refresh_tokens": 300_000}}})
            self.assertIsNone(self.z.context_check(s, None, {}))
            self.assertIsNone(self.z.context_check({**s, "runtime": "copilot"}, None, {}))

    def test_flagged_worker_restarts_only_when_idle_with_nothing_pending(self):
        conn = self.session()
        conn.execute("UPDATE sessions SET status='running'")
        conn.commit()
        self._conn = conn
        s = conn.execute("SELECT * FROM sessions").fetchone()
        alerts = {1: {"refresh": 180_000}}
        with patch.object(self.z, "cmd_handoff", return_value=7) as handoff, patch.object(self.z, "read_screen", return_value="done thinking"):
            conn.execute("INSERT INTO questions(task_id,session_id,question,asked_at) VALUES (1,1,'q','now')")
            self.assertIsNone(self.z.settle_turn(conn, s, "x", alerts, "L", 5))  # a question is pending: wait
            handoff.assert_not_called()
            conn.execute("UPDATE questions SET status='answered'")
            line = self.z.settle_turn(conn, s, "x", alerts, "L", 5)
            handoff.assert_called_once_with(["1", "restart"])
        self.assertIn("restarted fresh as S7", line)
        self.assertIn("180k", line)
        with patch.object(self.z, "cmd_handoff", side_effect=SystemExit(1)):
            alerts = {1: {"refresh": 180_000}}
            self.assertIn("restart FAILED", self.z.settle_turn(conn, s, "x", alerts, "L", 5))

    # ---- cleanup at close

    def git(self, path, *args):
        return subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              check=True, capture_output=True, text=True).stdout

    def repo_with_worktree(self):
        repo = self.root / "repo"
        repo.mkdir()
        self.git(repo, "init", "-b", "main")
        (repo / "a.txt").write_text("a")
        self.git(repo, "add", "."), self.git(repo, "commit", "-m", "init")
        remote = self.root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        self.git(repo, "remote", "add", "origin", str(remote)), self.git(repo, "push", "-q", "origin", "main")
        wt = self.root / "wt"
        self.git(repo, "worktree", "add", "-b", "feature/x", str(wt))
        return repo, wt

    def closing(self, conn, repo, *args, in_place=False):
        proj = {"path": str(repo), **({"worktrees": False} if in_place else {})}
        with patch.object(self.z, "db", return_value=conn), patch.object(self.z, "require_lock"), \
                patch.object(self.z, "project", return_value=proj), patch.object(self.z, "close_workspace"), \
                patch.object(self.z, "record_credits"):
            self.z.cmd_close(["S1", *args])

    def worktree_session(self, repo, wt, scratch=None):
        conn = self.session()
        conn.execute("UPDATE sessions SET repo='r', worktree=?, scratch=? WHERE id=1", (str(wt), scratch))
        conn.commit()
        return conn

    def status(self, conn):
        return conn.execute("SELECT status FROM sessions WHERE id=1").fetchone()[0]

    def test_close_removes_a_landed_worktree_even_without_an_upstream(self):
        repo, wt = self.repo_with_worktree()  # branch is in origin/main, no upstream of its own
        conn = self.worktree_session(repo, wt)
        self.closing(conn, repo)
        self.assertFalse(wt.exists())
        self.assertEqual(self.status(conn), "closed")
        self.assertIn("removed worktree", self.output.getvalue())

    def test_close_keeps_a_worktree_with_unpushed_commits(self):
        repo, wt = self.repo_with_worktree()
        (wt / "b.txt").write_text("b")
        self.git(wt, "add", "."), self.git(wt, "commit", "-m", "work")
        conn = self.worktree_session(repo, wt)
        with self.assertRaises(SystemExit):
            self.closing(conn, repo)
        self.assertIn("1 commit(s) not pushed", self.output.getvalue())
        self.assertTrue(wt.exists())
        self.assertEqual(self.status(conn), "running")
        self.closing(conn, repo, "keep-worktree")
        self.assertTrue(wt.exists())
        self.assertEqual(self.status(conn), "closed")

    def scratch_session(self, name="T1-qa"):
        folder = self.root / "folder"
        scratch = folder / name
        scratch.mkdir(parents=True)
        conn = self.session()
        conn.execute("UPDATE sessions SET repo='docs', worktree=?, scratch=? WHERE id=1", (str(folder), str(scratch)))
        conn.commit()
        return folder, scratch, conn

    def test_close_removes_scratch_but_not_the_project_folder(self):
        folder, scratch, conn = self.scratch_session()
        (scratch / "clone").mkdir()
        (scratch / "clone" / "big.bin").write_text("x")
        (folder / "keep.md").write_text("docs")
        self.closing(conn, folder, in_place=True)
        self.assertFalse(scratch.exists())
        self.assertTrue((folder / "keep.md").exists())
        self.assertEqual(self.status(conn), "closed")

    def test_close_keeps_scratch_clone_with_unpushed_commits(self):
        folder, scratch, conn = self.scratch_session()
        clone = scratch / "pr-1"
        self.git(scratch, "init", "-b", "main", str(clone))
        (clone / "f").write_text("f")
        self.git(clone, "add", "."), self.git(clone, "commit", "-m", "mine")
        self.closing(conn, folder, in_place=True)
        self.assertTrue(clone.exists())
        self.assertIn("pr-1: commits not on any remote", self.output.getvalue())
        self.assertEqual(self.status(conn), "closed")

    def test_scratch_outside_its_project_or_oddly_named_is_never_deleted(self):
        folder, scratch, conn = self.scratch_session(name="notes")
        self.closing(conn, folder, in_place=True)
        self.assertTrue(scratch.exists())
        conn.execute("UPDATE sessions SET scratch=? WHERE id=1", (str(self.root),))
        self.assertIsNone(self.z.own_scratch(conn.execute("SELECT * FROM sessions").fetchone()))

    def feedback(self, conn, kind="comment", **fields):
        """Insert an inbox row the way the dashboard would; returns its id."""
        row = {"kind": kind, "task_id": 1, "text": "text", "created_at": "2026-10-26 12:00"} | fields
        cur = conn.execute(f"INSERT INTO inbox({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
        conn.commit()
        return cur.lastrowid

    def test_inbox_table_is_added_to_old_databases(self):
        self.z.DB_PATH.parent.mkdir()
        conn = sqlite3.connect(self.z.DB_PATH)
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT)")
        conn.close()
        conn = self.z.db()
        self.addCleanup(conn.close)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(inbox)")}
        self.assertTrue({"kind", "task_id", "question_id", "rating", "title", "jira", "repo", "text", "status",
                         "response", "created_at", "handled_at"} <= columns)

    def test_inbox_items_are_described_on_one_line(self):
        item = lambda **f: {"id": 3, "task_id": None, "question_id": None, "rating": None, "title": None,
                            "jira": None, "repo": None, "text": None} | f
        self.assertEqual(self.z.inbox_summary(item(kind="answer", question_id=12, task_id=14, text="Approve —\ngo ahead")),
                         '#3 answer to Q12 (T14): "Approve — go ahead"')
        self.assertEqual(self.z.inbox_summary(item(kind="comment", task_id=14, text="x" * 200), 20),
                         f'#3 comment on T14: "{"x" * 19}…"')
        self.assertEqual(self.z.inbox_summary(item(kind="rating", task_id=2, rating="good")), "#3 rating of T2: good")
        self.assertEqual(self.z.inbox_summary(item(kind="rating", task_id=2, rating="not_good", text="thin PR")),
                         '#3 rating of T2: not good: "thin PR"')
        self.assertEqual(self.z.inbox_summary(item(kind="task", title="Add retries", jira="MENT-1", repo="api")),
                         '#3 new task: "Add retries" (ticket MENT-1, repo api)')

    def test_status_lists_only_feedback_still_to_handle(self):
        conn = self.session()
        first = self.feedback(conn, text="Please also cover dark mode")
        self.feedback(conn, text="old", status="handled")
        status = self.z.render_status(conn, live={})
        self.assertIn("Dashboard feedback to handle", status)
        self.assertIn(f'#{first} comment on T1: "Please also cover dark mode"', status)
        self.assertNotIn('"old"', status)
        conn.execute("UPDATE inbox SET status='handled'")
        self.assertNotIn("Dashboard feedback", self.z.render_status(conn, live={}))

    def test_answering_a_question_closes_the_dashboard_answers_to_it(self):
        conn = self.session()
        conn.execute("INSERT INTO questions(id,task_id,session_id,question,asked_at) VALUES (5,1,1,'Ship it?','now')")
        mine = self.feedback(conn, "answer", question_id=5, text="yes")
        other = self.feedback(conn, "answer", question_id=6, text="unrelated")
        with patch.object(self.z, "db", return_value=conn), patch.object(self.z, "require_lock"):
            self.z.cmd_q(["answer", "5", "yes,", "ship", "it"])
        rows = {r["id"]: r for r in conn.execute("SELECT * FROM inbox")}
        self.assertEqual((rows[mine]["status"], rows[mine]["response"]), ("handled", "Recorded as the answer to Q5"))
        self.assertEqual(rows[other]["status"], "new")

    def test_inbox_lists_new_items_in_full_and_handled_ones_on_request(self):
        conn = self.session()
        self.feedback(conn, text="line one\nline two")
        self.feedback(conn, "task", task_id=None, title="Add retries", jira="MENT-1", text="Details")
        done = self.feedback(conn, text="settled")
        conn.execute("UPDATE inbox SET status='handled', handled_at='2026-10-26 13:00', response='Passed to S1' WHERE id=?", (done,))
        conn.commit()
        with patch.object(self.z, "db", return_value=conn):
            self.z.cmd_inbox([])
        listing = self.output.getvalue()
        self.assertIn("#1 [new] comment on T1 · 2026-10-26 12:00\n    line one\n    line two", listing)
        self.assertIn("#2 [new] new task · 2026-10-26 12:00\n    title: Add retries\n    ticket: MENT-1\n    Details", listing)
        self.assertNotIn("settled", listing)
        self.output.truncate(0)
        with patch.object(self.z, "db", return_value=conn):
            self.z.cmd_inbox(["list", "--all"])
        self.assertIn("→ handled 2026-10-26 13:00: Passed to S1", self.output.getvalue())

    def test_inbox_done_records_the_outcome_once_and_needs_the_lock(self):
        conn = self.session()
        item = self.feedback(conn, "rating", rating="not_good", text="PR description was thin")
        with patch.object(self.z, "db", return_value=conn):
            with self.assertRaises(SystemExit):
                self.z.cmd_inbox(["done", str(item)])
        self.assertEqual(conn.execute("SELECT status FROM inbox").fetchone()[0], "new")
        with patch.object(self.z, "db", return_value=conn), patch.object(self.z, "require_lock"):
            self.z.cmd_inbox(["done", str(item), "Recorded", "in", "lessons"])
            self.z.cmd_inbox(["done", str(item), "again"])
            for bad in (["done"], ["done", "x"], ["done", "99"], ["undo", "1"]):
                with self.assertRaises(SystemExit):
                    self.z.cmd_inbox(bad)
        row = conn.execute("SELECT * FROM inbox").fetchone()
        self.assertEqual((row["status"], row["response"]), ("handled", "Recorded in lessons"))
        self.assertTrue(row["handled_at"])
        events = conn.execute("SELECT kind, text FROM events WHERE task_id=1").fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "feedback")
        self.assertIn('rating of T1: not good: "PR description was thin" → Recorded in lessons', events[0]["text"])
        self.assertIn("already handled", self.output.getvalue())

    def watch_once(self, *args):
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "live_agents", return_value={}):
            self.z.cmd_watch(["--once", *args])

    def test_watch_reports_each_inbox_item_once(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO tasks(title,created_at,updated_at) VALUES ('QA','now','now')")
        self.feedback(conn, text="Please also cover dark mode")
        self.feedback(conn, text="settled", status="handled")
        self.watch_once("0.01")
        self.assertIn('INBOX #1 comment on T1: "Please also cover dark mode" — handle it with the inbox skill',
                      self.output.getvalue())
        self.assertNotIn("settled", self.output.getvalue())
        with patch.object(self.z, "require_lock"):
            self.z.cmd_ack(["--all"])
        self.output.truncate(0)
        self.output.seek(0)
        with patch.object(self.z.time, "sleep", side_effect=InterruptedError):  # re-armed: nothing new, no repeat
            with self.assertRaises(InterruptedError):
                self.watch_once("0.01")
        self.assertEqual(self.output.getvalue(), "")
        self.feedback(conn, text="second")
        self.watch_once()
        self.assertIn("INBOX #3 comment on T1", self.output.getvalue())
        self.assertNotIn("INBOX #1 ", self.output.getvalue())

    def test_a_new_watch_reports_items_nobody_handled_yet(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        self.feedback(conn, text="still waiting")
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "live_agents", return_value={}), \
                patch.object(self.z.time, "sleep", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                self.z.cmd_watch(["0.01"])
        self.assertIn('INBOX #1 comment on T1: "still waiting"', self.output.getvalue())

    def test_watch_state_written_before_the_inbox_existed_still_loads(self):
        self.session()
        self.z.WATCH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.WATCH_STATE_PATH.write_text(json.dumps({"1": {"state": "idle", "sequence": 10}}))
        agent = {"agent_status": "idle", "state_change_seq": 10}
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "live_agents", return_value={"qa:p1": agent}), \
                patch.object(self.z.time, "sleep", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                self.z.cmd_watch(["--once", "0.01"])
        self.assertEqual(self.output.getvalue(), "")  # same snapshot as before: not a first run, nothing new
        self.assertEqual(json.loads(self.z.WATCH_STATE_PATH.with_name("watch-inbox.json").read_text()), [])


    def task(self, conn, status="active", title="QA"):
        cur = conn.execute("INSERT INTO tasks(title,status,created_at,updated_at) VALUES (?,?,'2026-10-26 12:00','2026-10-26 12:00')",
                           (title, status))
        conn.commit()
        return cur.lastrowid

    def question(self, conn, task_id=1, status="open"):
        cur = conn.execute("INSERT INTO questions(task_id,question,status,asked_at) VALUES (?,'Ship it?',?,'2026-10-26 12:00')",
                           (task_id, status))
        conn.commit()
        return cur.lastrowid

    def projects(self, *repos):
        self.z.PROJECTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.PROJECTS_PATH.write_text(json.dumps({"repos": {r: {} for r in repos}}))

    def inbox(self, conn):
        return [dict(r) for r in conn.execute("SELECT * FROM inbox ORDER BY id")]

    def test_dashboard_feedback_is_stored_in_the_inbox(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        self.projects("api", "web")
        task, delivered = self.task(conn), self.task(conn, "pr_open")
        question = self.question(conn, task)
        for data in [
            {"kind": "answer", "question_id": question, "text": "  Approve —\r\ngo ahead  "},
            {"kind": "comment", "task_id": task, "text": "Please also cover dark mode"},
            {"kind": "rating", "task_id": delivered, "rating": "good"},
            {"kind": "rating", "task_id": delivered, "rating": "not_good", "text": "The PR description was thin"},
            {"kind": "task", "title": " Add retries ", "jira": "MENT-1", "repo": "api", "text": "To the sync job"},
            {"kind": "task", "title": "Just a title", "jira": "", "repo": "", "text": ""},
        ]:
            self.z.add_feedback(conn, data)
        rows = self.inbox(conn)
        self.assertEqual([r["kind"] for r in rows], ["answer", "comment", "rating", "rating", "task", "task"])
        self.assertEqual((rows[0]["question_id"], rows[0]["task_id"], rows[0]["text"]), (question, task, "Approve —\ngo ahead"))
        self.assertEqual((rows[1]["task_id"], rows[1]["text"]), (task, "Please also cover dark mode"))
        self.assertEqual((rows[2]["rating"], rows[2]["text"]), ("good", None))
        self.assertEqual((rows[3]["rating"], rows[3]["text"]), ("not_good", "The PR description was thin"))
        self.assertEqual((rows[4]["title"], rows[4]["jira"], rows[4]["repo"], rows[4]["text"]),
                         ("Add retries", "MENT-1", "api", "To the sync job"))
        self.assertEqual((rows[5]["jira"], rows[5]["repo"], rows[5]["text"]), (None, None, None))
        self.assertTrue(all(r["status"] == "new" and r["created_at"] for r in rows))

    def test_dashboard_feedback_is_validated(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        self.projects("api")
        active, delivered = self.task(conn), self.task(conn, "done")
        open_q, answered_q = self.question(conn, active), self.question(conn, active, "answered")
        big = "x" * (self.z.FEEDBACK_TEXT_MAX + 1)
        for message, data in [
            ("unknown kind", {"kind": "delete"}),
            ("unknown kind", {}),
            ("The answer is required", {"kind": "answer", "question_id": open_q, "text": "  "}),
            ("already been answered", {"kind": "answer", "question_id": answered_q, "text": "yes"}),
            ("there is no question 99", {"kind": "answer", "question_id": 99, "text": "yes"}),
            ("there is no question '1'", {"kind": "answer", "question_id": str(open_q), "text": "yes"}),
            ("there is no task True", {"kind": "comment", "task_id": True, "text": "yes"}),
            (f"there is no task {10 ** 20}", {"kind": "comment", "task_id": 10 ** 20, "text": "yes"}),
            ("there is no task None", {"kind": "comment", "text": "yes"}),
            ("The comment must be text", {"kind": "comment", "task_id": active, "text": ["a"]}),
            ("The comment is required", {"kind": "comment", "task_id": active}),
            ("too long", {"kind": "comment", "task_id": active, "text": big}),
            ("only delivered work", {"kind": "rating", "task_id": active, "rating": "good"}),
            ("must be good or not_good", {"kind": "rating", "task_id": delivered, "rating": "great"}),
            ("must be good or not_good", {"kind": "rating", "task_id": delivered}),
            ("The reason is required", {"kind": "rating", "task_id": delivered, "rating": "not_good", "text": " "}),
            ("The title is required", {"kind": "task", "title": " ", "text": "details"}),
            ("The title is too long", {"kind": "task", "title": "t" * (self.z.FEEDBACK_TITLE_MAX + 1)}),
            ("unknown repo 'other'", {"kind": "task", "title": "T", "repo": "other"}),
            ("The ticket ID is too long", {"kind": "task", "title": "T", "jira": "J" * 41}),
        ]:
            with self.subTest(data=data):
                with self.assertRaisesRegex(self.z.FeedbackError, message):
                    self.z.add_feedback(conn, data)
        self.assertEqual(self.inbox(conn), [])

    def test_repos_for_the_new_task_form_tolerate_a_missing_or_broken_projects_file(self):
        self.assertEqual(self.z.dashboard_repos(), [])
        self.projects("web", "api")
        self.assertEqual(self.z.dashboard_repos(), ["api", "web"])
        self.z.PROJECTS_PATH.write_text("[1, 2]")
        self.assertEqual(self.z.dashboard_repos(), [])
        self.z.PROJECTS_PATH.write_text("{broken")
        self.assertEqual(self.z.dashboard_repos(), [])

    def test_herdr_agents_tell_unreachable_from_none_running(self):
        listing = lambda agents: subprocess.CompletedProcess([], 0, json.dumps({"result": {"agents": agents}}), "")
        with patch.object(self.z, "run", return_value=listing([{"pane_id": "w1:p1", "agent_status": "idle"}])):
            self.assertEqual(self.z.herdr_agents(), {"w1:p1": {"pane_id": "w1:p1", "agent_status": "idle"}})
        with patch.object(self.z, "run", return_value=listing([])):
            self.assertEqual(self.z.herdr_agents(), {})
        for unreachable in (subprocess.CompletedProcess([], 1, "", "server_unavailable"),
                            subprocess.CompletedProcess([], 0, "not json", ""),
                            subprocess.CompletedProcess([], 0, "{}", ""), FileNotFoundError("herdr")):
            with self.subTest(unreachable=unreachable):
                effect = {"side_effect": unreachable} if isinstance(unreachable, Exception) else {"return_value": unreachable}
                with patch.object(self.z, "run", **effect):
                    self.assertIsNone(self.z.herdr_agents())
                    self.assertEqual(self.z.live_agents(), {})

    def test_dashboard_state_groups_tasks_and_shows_live_workers(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        self.projects("api")
        working, parked, delivered = self.task(conn, "active", "Working"), self.task(conn, "parked", "Parked"), self.task(conn, "pr_open", "Delivered")
        stale = self.task(conn, "waiting_user", "Stale")
        for task, pane, status, pr in [(working, "w1:p1", "running", None), (stale, "w9:p1", "waiting_user", None),
                                       (delivered, "w2:p1", "closed", "https://example.test/pr/1")]:
            conn.execute("INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,pane_id,status,runtime,model,effort,pr_url,"
                         "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'now','now')",
                         (task, "api", "main", f"feature/t{task}", "/tmp/wt", f"t{task}-api", pane, status, "claude", "opus", "high", pr))
        conn.execute("INSERT INTO events(task_id,ts,kind,text) VALUES (?, '2026-10-26 12:01', 'milestone', 'Half way')", (working,))
        question = self.question(conn, working)
        conn.execute("INSERT INTO inbox(kind,task_id,question_id,text,created_at) VALUES ('answer',?,?,'yes','now')", (working, question))
        conn.commit()
        agents = {"w1:p1": {"agent_status": "blocked"}, "w2:p1": {"agent_status": "idle"}}
        with patch.object(self.z, "dashboard_zed", return_value={"active": True}):
            state = self.z.dashboard_state(conn, agents)
        json.dumps(state)  # everything the page gets must be plain JSON
        by_title = {g["title"]: g for g in state["groups"]}
        self.assertEqual(list(by_title), ["In progress", "PR open", "Not started / parked", "Recently finished"])
        self.assertEqual([t["title"] for t in by_title["In progress"]["tasks"]], ["Working", "Stale"])
        self.assertEqual([t["title"] for t in by_title["Not started / parked"]["tasks"]], ["Parked"])
        live = {s["id"]: s["live"] for g in state["groups"] for t in g["tasks"] for s in t["sessions"]}
        self.assertEqual(live, {1: "blocked", 2: "gone", 3: None})  # a closed session has no live state
        task = by_title["In progress"]["tasks"][0]
        self.assertEqual((task["sessions"][0]["runtime"], task["sessions"][0]["model"], task["sessions"][0]["effort"]),
                         ("claude", "opus", "high"))
        self.assertEqual([e["text"] for e in task["events"]], ["Half way"])
        self.assertEqual(by_title["PR open"]["tasks"][0]["sessions"][0]["pr_url"], "https://example.test/pr/1")
        self.assertTrue(by_title["PR open"]["tasks"][0]["can_rate"])
        self.assertFalse(task["can_rate"])
        self.assertEqual([(q["id"], q["task_title"], q["sent"]["text"]) for q in state["questions"]], [(question, "Working", "yes")])
        self.assertEqual((state["repos"], state["herdr"], state["pending_feedback"]), (["api"], True, 1))
        unreachable = self.z.dashboard_state(conn, None)
        self.assertFalse(unreachable["herdr"])
        self.assertEqual({s["live"] for g in unreachable["groups"] for t in g["tasks"] for s in t["sessions"]}, {"unavailable", None})

    def test_dashboard_state_shows_employees_hats_verdicts_and_plan_progress(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        self.projects("api")
        task = self.task(conn, "active", "Build it")
        for hat, employee, status, verdict in [("build", "pip", "closed", None), ("qa", "sol", "closed", "blockers"), ("review", "kit", "running", None)]:
            conn.execute("INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,status,employee,hat,verdict,created_at,updated_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,'now','now')", (task, "api", "main", "story/x", "/tmp/wt", f"{hat}-api", status, employee, hat, verdict))
        conn.commit()
        for name, senior in (("pip", "false"), ("kit", "true"), ("sol", "false")):
            (self.root / "employees" / name).mkdir(parents=True)
            (self.root / "employees" / name / "employee.md").write_text(f"---\nname: {name}\nvoice: v\nsenior: {senior}\n---\n# {name}\n")
        (self.root / "briefs").mkdir()
        (self.root / "briefs" / f"T{task}-api-plan.md").write_text("- [x] one\n- [ ] two\n")
        with patch.object(self.z.emp, "employees_dir", return_value=self.root / "employees"):
            state = self.z.dashboard_state(conn, {})
        json.dumps(state)
        team = {e["name"]: e for e in state["team"]["employees"]}
        self.assertEqual(team["kit"]["assignment"]["hat"], "review")
        self.assertTrue(team["kit"]["senior"])
        self.assertIsNone(team["sol"]["assignment"])
        self.assertEqual([w["kind"] for w in state["team"]["waits"]], ["fix"])
        task_json = state["groups"][0]["tasks"][0]
        self.assertEqual(task_json["plan"], {"done": 1, "total": 2})
        self.assertEqual([(s["employee"], s["hat"], s["verdict"]) for s in task_json["sessions"]],
                         [("pip", "build", None), ("sol", "qa", "blockers"), ("kit", "review", None)])

    def test_dashboard_state_limits_finished_tasks_and_reports_ratings(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        for n in range(25):
            self.task(conn, "done", f"Done {n}")
        conn.execute("INSERT INTO inbox(kind,task_id,rating,text,created_at) VALUES ('rating',25,'not_good','first','now')")
        conn.execute("INSERT INTO inbox(kind,task_id,rating,text,created_at) VALUES ('rating',25,'good','changed my mind','now')")
        conn.execute("UPDATE tasks SET updated_at='2026-10-27 09:00' WHERE id=25")
        conn.commit()
        finished = self.z.dashboard_state(conn, {})["groups"][-1]
        self.assertEqual((len(finished["tasks"]), finished["total"]), (20, 25))
        self.assertEqual(finished["tasks"][0]["title"], "Done 24")  # most recently updated first
        self.assertEqual(finished["tasks"][0]["rating"]["text"], "changed my mind")
        self.assertEqual(len(finished["tasks"][0]["feedback"]), 2)

    def test_dashboard_says_whether_a_zed_session_is_listening(self):
        self.assertEqual(self.z.dashboard_zed(), {"active": False, "runtime": None, "since": None, "limited": {}})
        lock = {"pid": 1, "runtime": "copilot", "acquired_at": "2026-10-26 08:00"}
        self.z.RUNTIME_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.RUNTIME_STATE_PATH.write_text(json.dumps({"claude": {"until": "3pm", "note": "limit"}, "junk": 3}))
        with patch.object(self.z, "read_lock", return_value=lock), patch.object(self.z, "holder_alive", return_value=True):
            zed = self.z.dashboard_zed()
        self.assertEqual((zed["active"], zed["runtime"], zed["since"]), (True, "copilot", "2026-10-26 08:00"))
        self.assertEqual(zed["limited"], {"claude": {"until": "3pm", "note": "limit"}})
        with patch.object(self.z, "read_lock", return_value=lock), patch.object(self.z, "holder_alive", return_value=False):
            self.assertFalse(self.z.dashboard_zed()["active"])  # the holder is gone: a stale lock
        self.z.RUNTIME_STATE_PATH.write_text("{broken")
        self.assertEqual(self.z.dashboard_zed()["limited"], {})

    def serve(self, token="secret"):
        server = self.z.dashboard_server(0, token)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def fetch(self, server, method, path, body=None, headers=None, token="secret"):
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.request(method, path, body=body, headers=({"X-Zed-Token": token} if token else {}) | (headers or {}))
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res, data

    def post(self, server, payload, **kw):
        res, data = self.fetch(server, "POST", "/api/feedback", json.dumps(payload), {"Content-Type": "application/json"}, **kw)
        return res.status, (json.loads(data) if res.getheader("Content-Type", "").startswith("application/json") else data.decode())

    def test_dashboard_listens_on_localhost_only(self):
        server = self.serve()
        self.assertEqual(server.server_address[0], "127.0.0.1")

    def test_dashboard_needs_the_token_and_a_local_host_header(self):
        server = self.serve()
        port = server.server_address[1]
        for path, token in [("/", None), ("/", "wrong"), ("/api/state", None), ("/api/state", "wrong"), ("/nothing", None)]:
            with self.subTest(path=path, token=token):
                self.assertEqual(self.fetch(server, "GET", path, token=token)[0].status, 403)
        for host in ("evil.example", f"evil.example:{port}", f"127.0.0.1.evil.example:{port}", "127.0.0.1"):
            with self.subTest(host=host):
                self.assertEqual(self.fetch(server, "GET", "/api/state", headers={"Host": host})[0].status, 403)
                self.assertEqual(self.fetch(server, "GET", "/?token=secret", headers={"Host": host}, token=None)[0].status, 403)
        for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self.assertEqual(self.fetch(server, "GET", "/api/state", headers={"Host": host})[0].status, 200)
        self.assertEqual(self.fetch(server, "GET", "/?token=secret", token=None)[0].status, 200)  # how the page is opened
        self.assertEqual(self.post(server, {"kind": "comment", "task_id": 1, "text": "x"}, token=None)[0], 403)
        self.assertEqual(self.post(server, {"kind": "comment", "task_id": 1, "text": "x"}, token="wrong")[0], 403)
        self.assertEqual(self.fetch(server, "GET", "/api/state", token="tökén")[0].status, 403)

    def test_dashboard_serves_the_page_with_a_strict_policy(self):
        server = self.serve()
        res, body = self.fetch(server, "GET", "/?token=secret", token=None)
        csp = res.getheader("Content-Security-Policy")
        nonce = csp.split("script-src 'nonce-")[1].split("'")[0]
        self.assertEqual(len(nonce), 24)
        self.assertIn(f"style-src 'nonce-{nonce}'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertIn("default-src 'none'", csp)
        page = body.decode()
        self.assertIn(f'nonce="{nonce}"', page)
        self.assertNotIn("{{NONCE}}", page)
        self.assertEqual(res.getheader("Content-Type"), "text/html; charset=utf-8")
        for header, value in [("Cache-Control", "no-store"), ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer"),
                              ("X-Content-Type-Options", "nosniff")]:
            self.assertEqual(res.getheader(header), value)
        again = self.fetch(server, "GET", "/?token=secret", token=None)[0].getheader("Content-Security-Policy")
        self.assertNotEqual(again, csp)  # a fresh nonce every time

    def test_dashboard_api_returns_the_state(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        self.task(conn, "active", "Working")
        server = self.serve()
        with patch.object(self.z, "herdr_agents", return_value={}):
            res, body = self.fetch(server, "GET", "/api/state")
        self.assertEqual((res.status, res.getheader("Content-Type")), (200, "application/json; charset=utf-8"))
        self.assertEqual(json.loads(body)["groups"][0]["tasks"][0]["title"], "Working")

    def test_dashboard_feedback_endpoint_writes_the_inbox_and_nothing_else(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        self.projects("api")
        task = self.task(conn, "active")
        question = self.question(conn, task)
        before = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in ("tasks", "sessions", "questions", "events")}
        server = self.serve()
        status, body = self.post(server, {"kind": "answer", "question_id": question, "text": "Approve"})
        self.assertEqual((status, body), (200, {"id": 1}))
        self.assertEqual(self.post(server, {"kind": "comment", "task_id": task, "text": "Hello"}), (200, {"id": 2}))
        self.assertEqual(self.post(server, {"kind": "task", "title": "New", "repo": "api"}), (200, {"id": 3}))
        status, body = self.post(server, {"kind": "comment", "task_id": 99, "text": "Hello"})
        self.assertEqual((status, body), (400, {"error": "there is no task 99"}))
        self.assertEqual([r["kind"] for r in self.inbox(conn)], ["answer", "comment", "task"])
        after = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in before}
        self.assertEqual(after, before)  # the question is still open: only Zed answers it, via `q answer`

    def test_dashboard_feedback_endpoint_rejects_bad_requests(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        self.task(conn)
        server = self.serve()
        json_type = {"Content-Type": "application/json"}
        for label, body, headers, expected in [
            ("form post", "kind=comment&task_id=1&text=x", {"Content-Type": "application/x-www-form-urlencoded"}, 415),
            ("no content type", '{"kind": "comment"}', {}, 415),
            ("not json", "{broken", json_type, 400),
            ("a list", "[1]", json_type, 400),
            ("a bare string", '"hi"', json_type, 400),
        ]:
            with self.subTest(label):
                self.assertEqual(self.fetch(server, "POST", "/api/feedback", body, headers)[0].status, expected)
        # Declared sizes are checked before anything is read.
        for length, expected in [("0", 413), ("-5", 413), (str(self.z.DASHBOARD_BODY_MAX + 1), 413), ("lots", 411)]:
            with self.subTest(length=length):
                res, _ = self.fetch(server, "POST", "/api/feedback", None, json_type | {"Content-Length": length})
                self.assertEqual(res.status, expected)
        self.assertEqual(self.fetch(server, "POST", "/api/state", "{}", json_type)[0].status, 404)
        self.assertEqual(self.fetch(server, "GET", "/api/feedback")[0].status, 404)
        self.assertEqual(self.fetch(server, "PUT", "/api/feedback", "{}", json_type)[0].status, 501)
        res, _ = self.fetch(server, "OPTIONS", "/api/feedback", headers={"Origin": "https://evil.example"})
        self.assertEqual((res.status, res.getheader("Access-Control-Allow-Origin")), (501, None))  # no CORS: other sites can't call it
        self.assertEqual(self.inbox(conn), [])

    def test_dashboard_reports_a_busy_database_and_unexpected_errors(self):
        server = self.serve()
        with patch.object(self.z, "dashboard_state", side_effect=sqlite3.OperationalError("database is locked")):
            res, body = self.fetch(server, "GET", "/api/state")
        self.assertEqual(res.status, 503)
        self.assertIn("database is locked", json.loads(body)["error"])
        with patch.object(self.z, "dashboard_state", side_effect=RuntimeError("boom")), \
                patch("traceback.print_exc"):
            res, body = self.fetch(server, "GET", "/api/state")
        self.assertEqual((res.status, json.loads(body)["error"]), (500, "RuntimeError: boom"))

    def dashboard_process(self, *args):
        env = {**os.environ, "ZED_PROFILE_DIR": str(self.root)}
        proc = subprocess.Popen([sys.executable, "-B", str(HOME / "bin/zed"), "dashboard", *args], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.stderr.close)
        return proc

    def test_dashboard_command_serves_records_itself_and_cleans_up(self):
        proc = self.dashboard_process("--port", "0")
        url = proc.stdout.readline().split("Zed dashboard: ")[1].strip()
        self.assertRegex(url, r"^http://127\.0\.0\.1:\d+/\?token=[0-9a-f]{36}$")
        state = json.loads(self.z.DASHBOARD_STATE_PATH.read_text())
        self.assertEqual((state["url"], state["pid"]), (url, proc.pid))
        conn = http.client.HTTPConnection("127.0.0.1", state["port"], timeout=5)
        conn.request("GET", url.split(str(state["port"]))[1])
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()
        # Started again, it points at the running one instead of starting a second.
        again = self.dashboard_process("--port", "0")
        self.assertEqual(again.wait(timeout=10), 0)
        self.assertIn(f"already running (pid {proc.pid}): {url}", again.stdout.read())
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=10), 0)
        self.assertFalse(self.z.DASHBOARD_STATE_PATH.exists())

    def test_dashboard_command_ignores_a_stale_record_and_reports_a_busy_port(self):
        self.z.DASHBOARD_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        for stale in ("{broken", json.dumps({"url": "u", "pid": 2 ** 22 + 12345, "port": 1}), "[]", "null"):
            with self.subTest(stale=stale):
                self.z.DASHBOARD_STATE_PATH.write_text(stale)
                self.assertIsNone(self.z.running_dashboard())
        busy = socket.socket()
        self.addCleanup(busy.close)
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        proc = self.dashboard_process("--port", str(busy.getsockname()[1]))
        self.assertEqual(proc.wait(timeout=10), 1)
        self.assertIn("can't listen on 127.0.0.1", proc.stderr.read())
        for bad in ("70000", "-1", "abc"):
            self.assertEqual(self.dashboard_process("--port", bad).wait(timeout=10), 2)


if __name__ == "__main__":
    unittest.main()
