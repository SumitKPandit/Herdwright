"""`bin/zed spawn hat=`: the one-writer rule, the plan gate, the files a hat session starts with and the model policy.
herdr and the host load check are stubbed; git and the guard installation are real, in a throwaway repo and profile."""
import contextlib
import io
import json
import os
import re
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
        self.conn.execute("INSERT INTO tasks(id,title,ticket,worktype,status,created_at,updated_at) VALUES (20,'Role guard','ABC-20','story','active',?,?)", (ts, ts))
        self.conn.commit()
        self.brief = self.root / "brief.md"
        self.brief.write_text(BRIEF)
        self.branch = "story/abc-20-guard"
        self.herdr_calls = []
        real_run = self.z.run

        def fake_run(cmd, check=True, cwd=None):
            if cmd[0] == "herdr":
                self.herdr_calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return real_run(cmd, check=check, cwd=cwd)

        def fake_herdr_json(args):
            self.herdr_calls.append(["herdr", *args])
            return {"result": {"workspace": {"workspace_id": "w1"}, "root_pane": {"pane_id": "p1"},
                               "agent": {"agent_status": "idle"}}}

        for target, kwargs in (("run", {"side_effect": fake_run}), ("herdr_json", {"side_effect": fake_herdr_json}),
                               ("require_lock", {}), ("require_capacity", {}),
                               ("prompt_worker", {"return_value": (True, "")})):
            patcher = patch.object(self.z, target, **kwargs)
            setattr(self, f"mock_{target}", patcher.start())
            self.addCleanup(patcher.stop)
        patcher = patch.object(self.z.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        wt = self.wt_root / "api" / "abc-20-guard"
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
        self.assertIn(f"are in {HOME / 'bin'}: call them by that path when they are not on your PATH", prompt)  # S339: no bin/ on a worker's PATH
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


class FakeOpenCodeServer:
    """Stands in for `opencode_api`: a worker's server as OpenCode v2.2.7 answered in real runs (session creation echoes
    the model; a tool call stopped by a plugin comes back with status `error` and the thrown text as the message, one
    that ran with status `completed`; `executed` is false in both). `guard`: live (the deny plugin stops the canary), dead (the call runs) or silent (the model makes no call)."""

    def __init__(self, case, guard="live", up=True, model=None):
        self.case, self.guard, self.up, self.model, self.calls = case, guard, up, model, []

    def __call__(self, server, method, path, body=None, timeout=30):
        self.calls.append((method, path, body))
        self.case.herdr_calls.append(["api", method, path])  # one timeline with the herdr calls
        if not self.up:
            raise ConnectionRefusedError("connection refused")
        if (method, path) == ("POST", "/session"):
            return {"id": "ses_test1", "model": self.model or body["model"]}
        if path.endswith("/prompt"):
            return {"id": f"msg_{len(self.calls)}"}
        if path.endswith("/message"):
            command = re.search(r"zed-(deny|pretool)-canary \w+", self.prompts()[0]).group(0)
            if self.guard == "silent":
                return [{"type": "assistant", "content": [{"type": "text", "text": "CHECKED"}]}]
            if self.guard == "refused":  # the turn never ran: the message carries the provider's error
                return [{"type": "assistant", "content": [], "error": {"type": "provider", "message": "429 usage limit reached"}}]
            denied = self.guard == "live"
            state = {"status": "error" if denied else "completed", "input": {"command": command}}
            if denied:
                state["error"] = {"type": "unknown", "message": f"{command.rsplit('-', 1)[0]}: canary: `{command}` is denied, so the guard is live"}
            return [{"type": "user", "text": "x"}, {"type": "assistant", "content": [
                {"type": "reasoning", "text": "x"}, {"type": "tool", "name": "shell", "executed": False, "state": state}]}]
        return {"version": "2.2.7"} if path == "/info" else None

    def prompts(self):
        return [body["text"] for method, path, body in self.calls if path.endswith("/prompt")]


class OpenCodeTests(AssignCase):
    def setUp(self):
        super().setUp()
        self.server = FakeOpenCodeServer(self)
        patcher = patch.object(self.z, "opencode_api", side_effect=lambda *a, **k: self.server(*a, **k))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.kit_dir = self.profile / ".cache" / "hats" / "review-t20-opencode"

    def start_call(self):
        return next(c for c in self.herdr_calls if c[:3] == ["herdr", "agent", "start"])

    def after_dashes(self, call):
        return call[call.index("--") + 1:]

    def refused_start(self, text, **extra):
        """A spawn that created the session row but refused the worker: nothing started in its pane, no brief."""
        self.refused("review", text, runtime="opencode", **extra)
        s = self.conn.execute("SELECT * FROM sessions").fetchone()
        self.assertEqual(s["status"], "failed")
        self.assertNotIn(["herdr", "agent", "start"], [c[:3] for c in self.herdr_calls])
        self.assertFalse([p for p in self.server.prompts() if "engineering worker" in p])
        self.mock_prompt_worker.assert_not_called()
        self.assertIn(f"No brief was handed over and S{s['id']} is marked failed", self.output.getvalue())
        self.assertIn("FAILED to start", self.conn.execute("SELECT text FROM events WHERE kind='spawn'").fetchone()[0])
        return s

    def test_claude_stays_the_default_and_other_runtimes_are_refused(self):
        self.spawn("build")
        start = self.start_call()
        self.assertEqual((start[start.index("--kind") + 1], start[start.index("--timeout") + 1]), ("claude", "60000"))
        self.assertEqual(self.server.calls, [])
        self.refused("review", "runtime=pi is not supported: claude or opencode", runtime="pi")
        self.refused("review", 'takes no effort=: set "variant"', runtime="opencode", effort="high")

    def test_runtime_opencode_starts_a_private_server_a_session_with_model_and_variant_and_the_tui(self):
        self.mock_require_capacity.reset_mock()
        sid = self.spawn("review", runtime="opencode")
        workspace = next(c for c in self.herdr_calls if c[:3] == ["herdr", "workspace", "create"])
        env = dict(workspace[i + 1].split("=", 1) for i, a in enumerate(workspace) if a == "--env")
        self.assertEqual(env["OPENCODE_CONFIG_DIR"], str(self.kit_dir))
        self.assertTrue(env["OPENCODE_PASSWORD"])
        serve = next(c for c in self.herdr_calls if c[:3] == ["herdr", "pane", "run"])
        # in the worker's own pane, in the background: its shell commands run in this server, and the pane is its identity
        port = re.fullmatch(rf"opencode serve --print-logs --log-level warn --hostname 127\.0\.0\.1 --port (\d+) > {re.escape(str(self.kit_dir))}/server\.log 2>&1 &", serve[4]).group(1)
        self.assertEqual(serve[3], "p1")
        self.assertNotIn(["herdr", "pane", "split"], [c[:3] for c in self.herdr_calls])
        url = f"http://127.0.0.1:{port}"
        created = next(body for method, path, body in self.server.calls if (method, path) == ("POST", "/session"))
        self.assertEqual(created, {"location": {"directory": str(self.worktree)}, "title": "review-t20",
                                   "model": {"providerID": "opencode", "id": "nemotron-3.5-super-vl-free", "variant": "xhigh"}})
        start = self.start_call()
        self.assertEqual((start[start.index("--kind") + 1], start[start.index("--timeout") + 1]), ("opencode", "180000"))
        self.assertEqual(self.after_dashes(start), ["--server", url, "--auto", "--session", "ses_test1"])  # no --model: the TUI has none
        s = self.session(sid)
        self.assertEqual((s["status"], s["claude_session_id"], s["model"], s["effort"]),
                         ("running", "ses_test1", "opencode/nemotron-3.5-super-vl-free", "xhigh"))
        event = self.conn.execute("SELECT text FROM events WHERE kind='spawn'").fetchone()[0]
        self.assertIn("model opencode/nemotron-3.5-super-vl-free variant xhigh (opencode.json)", event)
        self.assertIn("runtime opencode, session ses_test1", event)
        self.assertFalse((self.kit_dir.parent / "review-t20-settings.json").exists())
        self.mock_require_capacity.assert_not_called()  # claude.json's max_running counts Claude sessions

    def test_model_and_variant_come_from_the_profiles_opencode_json_and_model_can_be_given(self):
        self.refused("review", "an OpenCode model is written provider/model, not 'nemotron'", runtime="opencode", model="nemotron")
        (self.profile / "config" / "opencode.json").write_text(json.dumps({"model": "acme/m-1", "variant": "low"}))
        self.spawn("review", runtime="opencode")
        self.assertEqual(self.server.calls[1][2]["model"], {"providerID": "acme", "id": "m-1", "variant": "low"})
        (self.profile / "config" / "opencode.json").write_text(json.dumps({"variant": "high"}))  # a missing key keeps its default
        self.server.calls.clear()
        sid = self.spawn("qa", runtime="opencode", model="acme/sub/m-2")
        self.assertEqual(self.server.calls[1][2]["model"], {"providerID": "acme", "id": "sub/m-2", "variant": "high"})
        self.assertIn("model acme/sub/m-2 variant high (given to spawn)",
                      self.conn.execute("SELECT text FROM events WHERE kind='spawn' ORDER BY id DESC").fetchone()[0])
        self.assertEqual(self.session(sid)["model"], "acme/sub/m-2")

    def test_a_variant_outside_claudes_effort_list_is_passed_on_and_claude_still_refuses_it(self):
        (self.profile / "config" / "opencode.json").write_text(json.dumps({"model": "acme/m-1", "variant": "minimal"}))
        sid = self.spawn("review", runtime="opencode")
        self.assertEqual(self.server.calls[1][2]["model"], {"providerID": "acme", "id": "m-1", "variant": "minimal"})
        self.assertEqual(self.session(sid)["effort"], "minimal")
        self.refused("qa", "invalid effort 'minimal'", effort="minimal")  # Claude Code's own list still applies to it

    def test_the_default_model_and_variant_are_the_profile_templates(self):
        template = json.loads((_load.HOME / "profile.template" / "config" / "opencode.json").read_text())
        self.assertEqual(self.z.opencode_config(), template)  # no opencode.json in this profile
        self.assertEqual(set(template), {"model", "variant"})

    def test_the_canary_is_denied_before_the_tui_starts_and_the_brief_goes_through_the_server(self):
        sid = self.spawn("review", runtime="opencode")
        canary, brief = self.server.prompts()
        self.assertRegex(canary, r"exactly this command: zed-deny-canary [0-9a-f]{8}\n")
        order = [c[:3] for c in self.herdr_calls]
        self.assertLess(order.index(["api", "GET", "/session/ses_test1/message"]), order.index(["herdr", "agent", "start"]))
        self.assertIn(["api", "POST", "/experimental/session/ses_test1/wait"], order)  # the canary's turn has ended
        self.assertIn(f"You are the engineering worker, NOT the Zed coordinator, for session S{sid}", brief)
        self.assertIn("Your worker rules are already in your instructions", brief)  # loaded from the config dir, not read twice
        self.assertNotIn("review-t20-rules.md", brief)
        self.assertLess(order.index(["herdr", "agent", "start"]), len(order) - 1 - order[::-1].index(["api", "POST", "/session/ses_test1/prompt"]))
        self.mock_prompt_worker.assert_not_called()  # nothing is typed into the screen
        self.assertIn('"prompt_ok": true', self.output.getvalue())

    def test_a_worker_whose_guard_is_not_live_gets_no_brief_and_is_marked_failed(self):
        self.server.guard = "dead"  # the plugin did not load: OpenCode runs the call
        s = self.refused_start("was not denied (status completed): the deny plugin is not live")
        self.assertEqual(s["claude_session_id"], "ses_test1")

    def test_a_model_that_makes_no_canary_call_proves_nothing(self):
        self.server.guard = "silent"
        self.refused_start("the model made no canary call, so the deny plugin is not proven live")

    def test_a_turn_the_provider_refused_is_reported_with_its_error(self):
        """S339: a usage limit showed only as "the model made no canary call"."""
        self.server.guard = "refused"
        self.refused_start("the model made no canary call, so the deny plugin is not proven live; the turn failed: ")
        self.assertIn("429 usage limit reached", self.output.getvalue())

    def test_a_server_that_does_not_answer_fails_the_worker(self):
        self.server.up = False
        with patch.object(self.z, "OPENCODE_SERVER_SECONDS", 0):
            s = self.refused_start("its OpenCode server at http://127.0.0.1:")
        self.assertIsNone(s["claude_session_id"])

    def test_a_session_on_another_model_fails_the_worker(self):
        self.server.model = {"providerID": "opencode", "id": "something-else"}
        self.refused_start("the session runs {'providerID': 'opencode', 'id': 'something-else'}, not the configured")

    def test_a_tui_that_does_not_become_ready_fails_the_worker_after_the_canary(self):
        stubbed = self.mock_run.side_effect
        self.mock_run.side_effect = lambda cmd, **k: (subprocess.CompletedProcess(cmd, 1, "", "agent_not_ready")
                                                      if cmd[:3] == ["herdr", "agent", "start"] else stubbed(cmd, **k))
        self.refused("review", "agent start did not report ready", runtime="opencode")
        self.assertEqual(self.conn.execute("SELECT status FROM sessions").fetchone()[0], "failed")
        self.assertEqual(len(self.server.prompts()), 1)  # the canary only

    def test_rules_reach_the_worker_through_its_config_dir_and_the_repos_agents_md_is_untouched(self):
        (self.worktree / "AGENTS.md").write_text("# The repository's own instructions\n")
        (self.profile / "config" / "settings.json").write_text(json.dumps({"worker": {"deny_tools": ["tracker_createIssue"]}}))
        self.spawn("review", runtime="opencode")
        rules = (self.kit_dir / "AGENTS.md").read_text()  # OpenCode loads <OPENCODE_CONFIG_DIR>/AGENTS.md as its global file
        self.assertIn("# You are a Zed worker", rules)
        self.assertIn("# Hat: review", rules)
        self.assertIn("## You run on OpenCode", rules)
        self.assertIn("does not read `CLAUDE.md`", rules)
        self.assertTrue(rules.startswith((self.kit_dir.parent / "review-t20-rules.md").read_text()))
        self.assertEqual((self.worktree / "AGENTS.md").read_text(), "# The repository's own instructions\n")
        self.assertEqual(zedlib.git(self.worktree, "status", "--porcelain").stdout.strip(), "?? AGENTS.md")  # nothing else written
        plugin = (self.kit_dir / "plugins" / "zed-deny" / "index.js").read_text()
        self.assertIn('"ZED_HAT": "review"', plugin)
        self.assertIn(f'"ZED_WORKTREE": "{self.worktree}"', plugin)
        self.assertIn('const DENY_TOOLS = ["tracker_createIssue"];', plugin)

    def test_session_identity_follows_opencode_env_and_a_claude_start_arms_no_watcher(self):
        with patch.dict(os.environ, {"OPENCODE_SESSION_ID": "oc-1", "OPENCODE_PID": "4242"}, clear=False):
            for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PID"):
                os.environ.pop(k, None)
            with patch.object(self.z, "pane_session", return_value=None):
                self.assertEqual((self.z.me()["agent_session"], self.z.me()["pid"]), ("oc-1", 4242))
        self.conn.execute("INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,status,created_at,updated_at) "
                          "VALUES (20,'api','main','b','/w','a','running','x','x')")
        self.conn.commit()
        armed = []  # a Claude Zed relies on its Stop hooks: start does not arm (an OpenCode one: test_zed_coordinator)
        with patch.object(self.z, "holds_lock", return_value=True), patch.object(self.z, "cmd_lock"), \
                patch.object(self.z, "live_agents", return_value={}), patch.object(self.z, "queue_pending", return_value=[]), \
                patch.object(self.z, "script_findings", return_value=[]), \
                patch.object(self.z, "watcher_alive", return_value=None), \
                patch.object(self.z, "arm_watcher", side_effect=lambda: armed.append(1)):
            self.z.cmd_start([])
        self.assertEqual(armed, [])
        self.assertIn("watcher: NOT RUNNING — arm it: bin/zed watch --once (background)", self.output.getvalue())


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
        self.assertEqual(settings["permissions"]["deny"], [])  # no profile setting: nothing is denied by name

    def test_claude_settings_deny_the_tools_the_profile_names(self):
        tools = ["mcp__tracker__createIssue", "mcp__wiki__updatePage"]
        (self.profile / "config" / "settings.json").write_text(json.dumps({"worker": {"deny_tools": tools}}))
        kit, _ = self.kit("build")
        self.assertEqual(json.loads(kit["claude_settings"].read_text())["permissions"]["deny"], tools)


if __name__ == "__main__":
    unittest.main()
