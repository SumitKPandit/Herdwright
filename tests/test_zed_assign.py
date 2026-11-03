"""`bin/zed spawn hat=`: the one-writer rule, the plan gate, the files a hat session starts with and the model policy.
herdr and the host load check are stubbed; git and the guard installation are real, in a throwaway repo and profile."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import _load  # noqa: F401  (puts bin/lib on sys.path)

import zedlib

HOME = Path(__file__).resolve().parents[1]
BRIEF = "# Brief\n\n## Plan\n- one\n\n## Definition of done\n- done\n"


class AssignCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.profile = self.root / "profile"
        self.repo = self.root / "api"
        self.wt_root = self.root / "wt"
        (self.profile / "config").mkdir(parents=True)
        (self.profile / "profile.md").write_text("# test profile\n")
        self.repo.mkdir()
        for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            zedlib.git(self.repo, *cmd)
        (self.repo / "guard.py").write_text("def require_role(user): ...\n")
        zedlib.git(self.repo, "add", ".")
        zedlib.git(self.repo, "commit", "-qm", "init")
        (self.profile / "config" / "projects.json").write_text(json.dumps(
            {"worktree_root": str(self.wt_root), "repos": {"api": {"path": str(self.repo), "dev_base": "main"}}}))
        (self.profile / "config" / "claude.json").write_text(json.dumps({"model": "claude-opus-5-5", "effort": "high"}))
        for name, value in (("PROFILE_DIR", self.profile), ("DB_PATH", self.profile / "db" / "zed.db"),
                            ("PROJECTS_PATH", self.profile / "config" / "projects.json")):
            patcher = patch.object(zedlib, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.z = types.ModuleType("zed")
        self.z.__file__ = str(HOME / "bin/zed")
        with patch.dict(os.environ, {"ZED_PROFILE_DIR": str(self.profile)}):
            exec(compile((HOME / "bin/zed").read_text(), self.z.__file__, "exec"), self.z.__dict__)
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(contextlib.redirect_stderr(self.output))
        self.conn = self.z.db()
        self.addCleanup(self.conn.close)
        ts = "2026-10-27 09:00"
        self.conn.execute("INSERT INTO tasks(id,title,jira,worktype,status,created_at,updated_at) VALUES (20,'Role guard','MENT-20','story','active',?,?)", (ts, ts))
        self.conn.commit()
        self.brief = self.root / "brief.md"
        self.brief.write_text(BRIEF)
        self.branch = "story/ment-20-guard"
        self.herdr_calls = []
        real_run = self.z.run

        def fake_run(cmd, check=True, cwd=None):
            if cmd[0] == "herdr":
                self.herdr_calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return real_run(cmd, check=check, cwd=cwd)

        def fake_herdr_json(args):
            self.herdr_calls.append(["herdr", *args])
            return {"result": {"workspace": {"workspace_id": "w1"}, "root_pane": {"pane_id": "p1"}}}

        for target, kwargs in (("run", {"side_effect": fake_run}), ("herdr_json", {"side_effect": fake_herdr_json}),
                               ("require_lock", {}), ("require_capacity", {}),
                               ("prompt_worker", {"return_value": (True, "")})):
            patcher = patch.object(self.z, target, **kwargs)
            setattr(self, f"mock_{target}", patcher.start())
            self.addCleanup(patcher.stop)
        patcher = patch.object(self.z.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        wt = self.wt_root / "api" / "ment-20-guard"
        wt.parent.mkdir(parents=True)
        zedlib.git(self.repo, "worktree", "add", "-q", "-b", self.branch, str(wt))
        self.worktree = wt

    def spawn(self, hat="build", **extra):
        args = ["20", "repo=api", "base=main", f"branch={self.branch}", f"brief={self.brief}",
                f"hat={hat}", *[f"{k}={v}" for k, v in extra.items()]]
        return self.z.cmd_spawn(args)

    def refused(self, hat, text, **extra):
        self.output.truncate(0)
        with self.assertRaises(SystemExit) as e:
            self.spawn(hat, **extra)
        self.assertEqual(e.exception.code, 1)
        self.assertIn(text, self.output.getvalue())

    def finish(self, sid, verdict=None):
        self.conn.execute("UPDATE sessions SET status='finished', verdict=? WHERE id=?", (verdict, sid))
        self.conn.commit()

    def session(self, sid):
        return self.conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()


class SpawnTests(AssignCase):
    def test_build_records_the_session_and_writes_its_files(self):
        sid = self.spawn("build")
        s = self.session(sid)
        self.assertEqual((s["hat"], s["agent"], s["core_start"]), ("build", "build-t20", None))
        out = self.profile / ".cache" / "hats"
        rules = (out / "build-t20-rules.md").read_text()
        self.assertIn("Hat: build", rules)
        self.assertNotIn("memory", rules.lower())
        workspace = next(c for c in self.herdr_calls if c[:3] == ["herdr", "workspace", "create"])
        env = [workspace[i + 1] for i, a in enumerate(workspace) if a == "--env"]
        self.assertEqual(env, ["ZED_HAT=build", f"ZED_SESSION=S{sid}", f"ZED_WORKTREE={self.worktree}", f"ZED_PROFILE_DIR={self.profile}"])
        start = next(c for c in self.herdr_calls if c[:3] == ["herdr", "agent", "start"])
        self.assertIn(str(out / "build-t20-rules.md"), start)
        self.assertEqual(start[start.index("--settings") + 1], str(out / "build-t20-settings.json"))
        hook = json.loads((out / "build-t20-settings.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual(hook["command"], str(HOME / "bin" / "zed-deny"))
        prompt = self.mock_prompt_worker.call_args.args[1]
        self.assertIn("You wear the build hat.", prompt)
        self.assertIn("zed-employee finish", prompt)
        self.assertIn(str(out / "build-t20-rules.md"), prompt)

    def test_hat_defaults_to_build_and_a_review_takes_hat_only(self):
        self.assertEqual(self.session(self.z.cmd_spawn(["20", "repo=api", "base=main", f"branch={self.branch}", f"brief={self.brief}"]))["hat"], "build")
        self.finish(1)
        self.assertEqual(self.session(self.spawn("review"))["hat"], "review")
        self.assertFalse(hasattr(self.z, "cmd_assign"))

    def test_a_review_or_qa_records_the_code_it_started_with_and_a_build_does_not(self):
        self.finish(self.spawn("build"))
        review = self.session(self.spawn("review"))
        self.assertEqual(review["core_start"], zedlib.core_tree(self.worktree))
        self.assertTrue(review["core_start"])

    def test_the_arguments_of_removed_features_are_refused_not_ignored(self):
        for extra in ({"employee": "juno"}, {"ephemeral": "why"}, {"tags": "a,b"}, {"origin": "user"}):
            self.refused("build", "spawn does not take", **extra)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_an_unknown_hat_is_refused(self):
        self.refused("juggle", "unknown hat")

    def test_a_review_session_is_measured_by_its_own_plan_and_zed_plan_t_id_reads_the_build_plan(self):
        build_plan = self.profile / "briefs" / "T20-api-plan.md"
        build_plan.parent.mkdir(exist_ok=True)
        build_plan.write_text("- [x] 1. a. Done when: x\n- [ ] 2. Raise PR\n")
        self.finish(self.spawn("build"))
        review_plan = self.profile / "briefs" / "T20-review-plan.md"
        review_plan.write_text("- [x] 1. Read the diff\n")
        self.brief.write_text(f"# Brief\n\n## Plan\n- write `{review_plan}` first\n\n## Definition of done\n- done\n")
        review = self.session(self.spawn("review"))
        self.assertEqual(self.z.session_plan_progress(self.conn, review), (1, 1))  # not the task's build progress (1, 2)
        zed_plan = _load.load("zed-plan")
        self.assertEqual(zed_plan.find_plan("T20"), build_plan)  # the review plan is never the task's plan

    def test_review_and_qa_wait_for_a_complete_build_plan(self):
        self.finish(self.spawn("build"))
        plan = self.profile / "briefs" / "T20-api-plan.md"
        plan.parent.mkdir(exist_ok=True)
        plan.write_text("- [x] 1. a. Done when: x\n- [ ] 2. Fix the parser. Done when: y\n- [ ] 3. Raise PR\n")
        self.refused("review", "review waits until the build is complete")
        self.refused("qa", "step 2: Fix the parser")
        plan.write_text("- [x] 1. a. Done when: x\n- [x] 2. Fix the parser. Done when: y\n- [ ] 3. Raise PR\n")
        self.assertEqual(self.session(self.spawn("review"))["hat"], "review")

    def test_a_reviewers_own_open_plan_never_holds_back_the_next_review_or_qa(self):
        build = self.spawn("build")
        self.assertEqual(self.session(build)["plan"], str(self.profile / "briefs" / "T20-api-plan.md"))  # the template default
        (self.profile / "briefs").mkdir(exist_ok=True)
        (self.profile / "briefs" / "T20-api-plan.md").write_text("- [x] 1. a. Done when: x\n- [ ] 2. Raise PR\n")
        self.finish(build)
        review_plan = self.profile / "briefs" / "T20-review-plan.md"
        review_plan.write_text("- [x] 1. Read the diff\n- [ ] 2. Run zed-employee finish and report (ZED:DONE)\n")
        self.brief.write_text(f"# Brief\n\n## Plan\n- write `{review_plan}` first\n\n## Definition of done\n- done\n")
        review = self.spawn("review")
        self.assertEqual(self.session(review)["plan"], str(review_plan))
        self.finish(review, "blockers")
        self.assertEqual(self.session(self.spawn("qa"))["hat"], "qa")  # the review plan's open step is not the build's

    def test_a_build_is_refused_while_a_reader_is_open_and_readers_may_share(self):
        self.finish(self.spawn("build"))
        reader = self.spawn("review")
        self.refused("build", "one writer per worktree")
        qa = self.spawn("qa")  # a second reader on the same worktree
        self.assertEqual(self.session(qa)["hat"], "qa")
        self.finish(reader)
        self.finish(qa)
        self.assertTrue(self.spawn("build"))  # nothing open any more

    def test_a_reader_is_refused_while_a_build_is_open(self):
        self.spawn("build")
        self.refused("review", "build-t20) is writing")

    def test_a_session_from_before_hats_on_the_worktree_counts_as_a_writer(self):
        self.conn.execute("INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,status,created_at,updated_at) "
                          "VALUES (20,'api','main',?,?,'t20-api','running','x','x')", (self.branch, str(self.worktree)))
        self.conn.commit()
        self.refused("review", "one writer per worktree")

    def test_a_same_named_open_session_gets_a_suffix(self):
        self.finish(self.spawn("build"))  # finished but not closed: its agent name is taken
        self.finish(self.spawn("review"))
        sid = self.spawn("review")
        self.assertEqual(self.session(sid)["agent"], f"review-t20-{sid}")

    def test_findings_of_earlier_sessions_are_named_in_the_first_prompt(self):
        self.z.log_event(self.conn, 20, "findings", "S2 review blockers: /tmp/findings.md")
        self.conn.commit()
        self.finish(self.spawn("review"))
        self.spawn("build")
        self.assertIn("/tmp/findings.md", self.mock_prompt_worker.call_args.args[1])


class WatcherAndStartTests(AssignCase):
    DONE = {"kind": "DONE", "items": [], "text": "x", "fields": {"Summary": "built it", "PR": "none", "QA": "ran the tests"}}

    def test_a_done_with_an_incomplete_plan_is_reported_and_a_raise_pr_step_is_not(self):
        sid = self.spawn("build")
        plan = self.profile / "briefs" / "T20-api-plan.md"
        plan.parent.mkdir(exist_ok=True)
        plan.write_text("- [x] 1. a. Done when: x\n- [ ] 2. Fix the parser. Done when: y\n- [ ] 3. Raise PR\n")
        text = self.z.record_block(self.conn, self.session(sid), self.DONE)
        self.assertIn("DONE with an incomplete plan: S1's plan (T20-api-plan.md) has 1 step(s)", text)
        plan.write_text("- [x] 1. a. Done when: x\n- [x] 2. Fix the parser. Done when: y\n- [ ] 3. Raise PR\n")
        self.assertNotIn("incomplete plan", self.z.record_block(self.conn, self.session(sid), self.DONE))

    def test_a_done_never_raises_a_write_back_line(self):
        sid = self.spawn("build")
        self.assertNotIn("write-back", self.z.record_block(self.conn, self.session(sid), self.DONE))

    def test_the_digest_and_status_show_each_session_with_its_own_plan_and_hat(self):
        build = self.spawn("build")
        (self.profile / "briefs").mkdir(exist_ok=True)
        (self.profile / "briefs" / "T20-api-plan.md").write_text("- [x] 1. a. Done when: x\n- [x] 2. b. Done when: y\n- [ ] 3. Raise PR\n")
        self.finish(build)
        review_plan = self.profile / "briefs" / "T20-review-plan.md"
        review_plan.write_text("- [x] 1. Read the diff\n- [ ] 2. Run the suite\n- [ ] 3. Run zed-employee finish and report (ZED:DONE)\n")
        self.brief.write_text(f"# Brief\n\n## Plan\n- write `{review_plan}` first\n\n## Definition of done\n- done\n")
        self.spawn("review")
        digest = "\n".join(self.z.render_brief(self.conn, live={}))
        self.assertIn("S2 T20 review-t20 running/GONE · review 1/3", digest)  # not the build plan's 2/2
        status = self.z.render_status(self.conn, live={})
        self.assertIn("plan `T20-api-plan.md`: 2/2 steps done (+1 awaiting merge/PR)", status)  # the task shows its build plan only
        self.assertIn("S2 review plan `T20-review-plan.md`: 1/3 steps done", status)
        self.assertNotIn("plan `T20-review-plan.md`", status.replace("S2 review plan `T20-review-plan.md`", ""))

    def test_start_reports_the_crew_but_no_employee_board(self):
        labels = []
        with patch.object(self.z, "holds_lock", return_value=True), patch.object(self.z, "cmd_lock"), \
                patch.object(self.z, "live_agents", return_value={}), patch.object(self.z, "watcher_alive", return_value=None), \
                patch.object(self.z, "queue_pending", return_value=[]), \
                patch.object(self.z, "script_findings", side_effect=lambda label, *a, **k: labels.append(label) or []):
            self.z.cmd_start([])
        self.assertEqual(labels, ["sessions out of step with the DB (zed-crew)", "core commits not on main yet (zed-core-sync)",
                                  "git guard (zed-guard)"])


class OpenCodeTests(AssignCase):
    def start_call(self):
        return next(c for c in self.herdr_calls if c[:3] == ["herdr", "agent", "start"])

    def test_claude_stays_the_default_and_other_runtimes_are_refused(self):
        self.spawn("build")
        start = self.start_call()
        self.assertEqual((start[start.index("--kind") + 1], start[start.index("--timeout") + 1]), ("claude", "60000"))
        self.refused("review", "runtime=pi is not supported: claude or opencode", runtime="pi")
        self.refused("review", "takes no effort=", runtime="opencode", effort="high")

    def test_runtime_opencode_starts_opencode_with_the_plugin_dir_and_no_claude_policy(self):
        self.mock_require_capacity.reset_mock()
        sid = self.spawn("review", runtime="opencode", model="anthropic/claude-sonnet-5-5")
        start = self.start_call()
        self.assertEqual((start[start.index("--kind") + 1], start[start.index("--timeout") + 1]), ("opencode", "180000"))
        self.assertEqual(start[start.index("--") + 1:], ["--model", "anthropic/claude-sonnet-5-5", "--auto"])  # no -n, no --settings
        workspace = next(c for c in self.herdr_calls if c[:3] == ["herdr", "workspace", "create"])
        env = [workspace[i + 1] for i, a in enumerate(workspace) if a == "--env"]
        out = self.profile / ".cache" / "hats"
        self.assertIn(f"OPENCODE_CONFIG_DIR={out / 'review-t20-opencode'}", env)
        self.assertTrue((out / "review-t20-opencode" / "plugins" / "zed-deny.js").is_file())
        self.assertFalse((out / "review-t20-settings.json").exists())
        self.mock_require_capacity.assert_not_called()  # claude.json's max_running counts Claude sessions
        self.assertIn("runtime opencode", self.conn.execute("SELECT text FROM events WHERE kind='spawn'").fetchone()[0])
        self.assertEqual(self.session(sid)["effort"], None)

    def test_session_identity_and_the_auto_armed_watcher_follow_opencode_env(self):
        with patch.dict(os.environ, {"OPENCODE_SESSION_ID": "oc-1", "OPENCODE_PID": "4242"}, clear=False):
            for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PID"):
                os.environ.pop(k, None)
            with patch.object(self.z, "pane_session", return_value=None):
                self.assertEqual((self.z.me()["agent_session"], self.z.me()["pid"]), ("oc-1", 4242))
            self.conn.execute("INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,status,created_at,updated_at) "
                              "VALUES (20,'api','main','b','/w','a','running','x','x')")
            self.conn.commit()
            armed = []
            with patch.object(self.z, "holds_lock", return_value=True), patch.object(self.z, "cmd_lock"), \
                    patch.object(self.z, "live_agents", return_value={}), patch.object(self.z, "queue_pending", return_value=[]), \
                    patch.object(self.z, "script_findings", return_value=[]), \
                    patch.object(self.z, "watcher_alive", return_value=None), \
                    patch.object(self.z, "arm_watcher", side_effect=lambda: armed.append(1) or 99):
                self.z.cmd_start([])
            self.assertEqual(armed, [1])
            self.assertIn("watcher: alive, pid 99", self.output.getvalue())
            os.environ.pop("OPENCODE_SESSION_ID")  # a Claude Zed relies on its Stop hooks: start does not arm
            armed.clear()
            with patch.object(self.z, "holds_lock", return_value=True), patch.object(self.z, "cmd_lock"), \
                    patch.object(self.z, "live_agents", return_value={}), patch.object(self.z, "queue_pending", return_value=[]), \
                    patch.object(self.z, "script_findings", return_value=[]), \
                    patch.object(self.z, "watcher_alive", return_value=None), \
                    patch.object(self.z, "arm_watcher", side_effect=lambda: armed.append(1)):
                self.z.cmd_start([])
            self.assertEqual(armed, [])


class RenderedPolicyTests(AssignCase):
    def kit(self, hat):
        return self.z.hat_kit(hat, 7, "review-t20", self.worktree)

    def test_claude_gets_a_settings_hook_with_the_deny_and_stop_guard(self):
        kit, env = self.kit("review")
        claude = self.z.claude_launch("review-t20", None, None, kit)
        self.assertEqual(claude[claude.index("--settings") + 1], str(kit["claude_settings"]))
        self.assertEqual(claude[claude.index("--append-system-prompt-file") + 1], str(kit["rules"]))
        settings = json.loads(kit["claude_settings"].read_text())
        self.assertEqual(settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"], str(HOME / "bin" / "zed-deny"))
        self.assertEqual(settings["hooks"]["Stop"][0]["hooks"][0]["command"], f"{HOME / 'bin' / 'zed-employee'} stop-guard")
        self.assertIn("ZED_HAT=review", env)


if __name__ == "__main__":
    unittest.main()
