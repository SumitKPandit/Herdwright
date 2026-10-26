import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
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
                      {"agent_status": "done", "completion_seq": 12}):
            with self.subTest(after=after):
                before = {"result": {"agent": {"completion_seq": 10}}}
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
                     "'claude',?,'now','now')", (str(self.root), str(self.root / "profile.md")))
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
            self.assertTrue(any(c.args[0] == ["herdr", "workspace", "close", "qa-ws"]
                                and c.kwargs.get("check", True) for c in run.call_args_list))
            note = (self.root / "briefs/T1-scratch-handoff-S1.md").read_text()
            self.assertIn("feature/qa", note)

    def test_handoff_refuses_to_spawn_if_old_workspace_cannot_close(self):
        conn = self.session()
        (self.root / "briefs").mkdir()

        def run(cmd, **kwargs):
            if cmd[:3] == ["herdr", "workspace", "close"]:
                raise SystemExit("workspace close failed")
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

    def test_watch_reports_fast_turns_once_and_records_first_idle(self):
        self.session()
        agent = {"agent_status": "idle", "state_change_seq": 10}
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "live_agents", return_value={"qa:p1": agent}), \
                patch.object(self.z, "read_screen", return_value="ZED:QUESTION\nWhich greeting?"):
            self.z.cmd_watch(["--once", "0.01"])
            self.assertIn("at watch start", self.output.getvalue())
            self.output.truncate(0)
            self.output.seek(0)
            with patch.object(self.z.time, "sleep", side_effect=InterruptedError):
                with self.assertRaises(InterruptedError):
                    self.z.cmd_watch(["--once", "0.01"])
            self.assertEqual(self.output.getvalue(), "")
            agent["state_change_seq"] = 12
            self.z.cmd_watch(["--once"])
            self.assertIn("qa", self.output.getvalue())

    def test_watch_exits_when_lock_is_lost(self):
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=False), \
                patch.object(self.z, "read_lock", return_value=None):
            with self.assertRaises(SystemExit) as error:
                self.z.cmd_watch(["--once"])
        self.assertEqual(error.exception.code, 3)
        self.assertIn("LOCK LOST", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
