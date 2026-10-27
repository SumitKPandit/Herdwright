import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
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

    def test_max_running_queues_extra_spawns_per_runtime(self):
        conn = self.session()
        self.config({"order": ["claude", "copilot"], "runtimes": {"claude": {"max_running": 1}}})
        with self.assertRaises(SystemExit) as e:
            self.z.require_capacity(conn, "claude", 2)
        self.assertEqual(e.exception.code, 75)
        self.assertIn("queued: 1 claude workers running", self.output.getvalue())
        self.z.require_capacity(conn, "copilot", 2)  # no cap configured
        conn.execute("UPDATE sessions SET status='closed'")
        self.z.require_capacity(conn, "claude", 2)

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


if __name__ == "__main__":
    unittest.main()
