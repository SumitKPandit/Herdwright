"""`bin/hw spawn hat=`: the one-writer rule, the plan gate, the files a hat session starts with and the model policy.
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

import hwlib

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
            hwlib.git(self.repo, *cmd)
        (self.repo / "guard.py").write_text("def require_role(user): ...\n")
        hwlib.git(self.repo, "add", ".")
        hwlib.git(self.repo, "commit", "-qm", "init")
        (self.profile / "config" / "projects.json").write_text(json.dumps(
            {"worktree_root": str(self.wt_root), "repos": {"api": {"path": str(self.repo), "dev_base": "main"}}}))
        (self.profile / "config" / "claude.json").write_text(json.dumps({"model": "claude-opus-5-5", "effort": "high"}))
        (self.profile / "config" / "opencode.json").write_text(json.dumps({"model": "acme/base", "variant": "xhigh"}))
        for name, value in (("PROFILE_DIR", self.profile), ("DB_PATH", self.profile / "db" / "hw.db"),
                            ("PROJECTS_PATH", self.profile / "config" / "projects.json")):
            patcher = patch.object(hwlib, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.z = types.ModuleType("hw")
        self.z.__file__ = str(HOME / "bin/hw")
        with patch.dict(os.environ, {"HW_PROFILE_DIR": str(self.profile)}):
            exec(compile((HOME / "bin/hw").read_text(), self.z.__file__, "exec"), self.z.__dict__)
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
        hwlib.git(self.repo, "worktree", "add", "-q", "-b", self.branch, str(wt))
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
        self.assertEqual(env, ["HW_HAT=build", f"HW_SESSION=S{sid}", f"HW_WORKTREE={self.worktree}", f"HW_PROFILE_DIR={self.profile}",
                               "PYTHONDONTWRITEBYTECODE=1"])  # a worker's test runs leave no `__pycache__/`
        start = next(c for c in self.herdr_calls if c[:3] == ["herdr", "agent", "start"])
        self.assertIn(str(out / "build-t20-rules.md"), start)
        self.assertEqual(start[start.index("--settings") + 1], str(out / "build-t20-settings.json"))
        hook = json.loads((out / "build-t20-settings.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual(hook["command"], str(HOME / "bin" / "hw-deny"))
        prompt = self.mock_prompt_worker.call_args.args[1]
        self.assertIn("You wear the build hat.", prompt)
        self.assertIn(f"are in {HOME / 'bin'}: call them by that path when they are not on your PATH", prompt)  # no bin/ on a worker's PATH
        self.assertIn("hw-employee finish", prompt)
        self.assertIn(str(out / "build-t20-rules.md"), prompt)


    def test_a_claude_worker_is_started_in_auto_permission_mode(self):
        """The README says so. Nobody watches a worker's pane to approve its tool calls; its hat's guard
        and its questions are what holds it."""
        self.spawn("build")
        start = next(c for c in self.herdr_calls if c[:3] == ["herdr", "agent", "start"])
        self.assertEqual(start[start.index("--permission-mode") + 1], "auto")

    def test_a_session_of_a_project_without_worktrees_is_handed_its_scratch_folder(self):
        """The guard of a review, QA or plan session cannot read which folder the session was given, so
        spawn says it (HW_SCRATCH, beside HW_SESSION). A worktree project has none (the test above)."""
        projects = self.profile / "config" / "projects.json"
        projects.write_text(json.dumps({"worktree_root": str(self.wt_root),
                                        "repos": {"api": {"path": str(self.repo), "dev_base": "main", "worktrees": False}}}))
        sid = self.spawn("qa")
        scratch = self.repo / "T20-qa-t20"
        self.assertTrue(scratch.is_dir())
        self.assertEqual(self.session(sid)["scratch"], str(scratch))
        workspace = next(c for c in self.herdr_calls if c[:3] == ["herdr", "workspace", "create"])
        self.assertIn(f"HW_SCRATCH={scratch}", workspace)
        self.assertIn(f"HW_WORKTREE={self.repo}", workspace)

    def test_hat_defaults_to_build_and_a_review_takes_hat_only(self):
        self.assertEqual(self.session(self.z.cmd_spawn(["20", "repo=api", "base=main", f"branch={self.branch}", f"brief={self.brief}"]))["hat"], "build")
        self.finish(1)
        self.assertEqual(self.session(self.spawn("review"))["hat"], "review")
        self.assertFalse(hasattr(self.z, "cmd_assign"))

    def test_a_review_or_qa_records_the_code_it_started_with_and_a_build_does_not(self):
        self.finish(self.spawn("build"))
        review = self.session(self.spawn("review"))
        self.assertEqual(review["core_start"], hwlib.core_tree(self.worktree))
        self.assertTrue(review["core_start"])

    def test_the_arguments_of_removed_features_are_refused_not_ignored(self):
        for extra in ({"employee": "juno"}, {"ephemeral": "why"}, {"tags": "a,b"}, {"origin": "user"}):
            self.refused("build", "spawn does not take", **extra)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_an_unknown_hat_is_refused(self):
        self.refused("juggle", "unknown hat")

    def test_a_review_session_is_measured_by_its_own_plan_and_hw_plan_t_id_reads_the_build_plan(self):
        build_plan = self.profile / "briefs" / "T20-api-plan.md"
        build_plan.parent.mkdir(exist_ok=True)
        build_plan.write_text("- [x] 1. a. Done when: x\n- [ ] 2. Raise PR\n")
        self.finish(self.spawn("build"))
        review = self.session(self.spawn("review"))
        Path(review["plan"]).write_text("- [x] 1. Read the diff\n")
        self.assertEqual(self.z.session_plan_progress(self.conn, review), (1, 1))  # not the task's build progress (1, 2)
        hw_plan = _load.load("hw-plan")
        self.assertEqual(hw_plan.find_plan("T20"), build_plan)  # the review plan is never the task's plan

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
        review = self.spawn("review")
        review_plan = self.profile / "briefs" / f"T20-review-S{review}-plan.md"
        self.assertEqual(self.session(review)["plan"], str(review_plan))
        review_plan.write_text("- [x] 1. Read the diff\n- [ ] 2. Run hw-employee finish and report (HW:DONE)\n")
        self.finish(review, "blockers")
        self.assertEqual(self.session(self.spawn("qa"))["hat"], "qa")  # the review plan's open step is not the build's

    def test_a_review_and_a_qa_on_one_brief_each_write_and_are_measured_by_their_own_plan(self):
        """Both read the build's brief, so both were given its plan file: a reviewer's finish was refused for
        QA's open steps, and each picked the other's step labels."""
        build = self.spawn("build")
        build_plan = Path(self.session(build)["plan"])
        build_plan.parent.mkdir(exist_ok=True)
        build_plan.write_text("- [x] 1. a. Done when: x\n- [ ] 2. Raise PR\n")
        self.finish(build)
        review, qa = self.spawn("review"), self.spawn("qa")
        plans = {hat: Path(self.session(sid)["plan"]) for hat, sid in (("review", review), ("qa", qa))}
        self.assertEqual(plans, {"review": self.profile / "briefs" / f"T20-review-S{review}-plan.md",
                                 "qa": self.profile / "briefs" / f"T20-qa-S{qa}-plan.md"})
        for hat, sid in (("review", review), ("qa", qa)):
            prompt = next(c.args[1] for c in self.mock_prompt_worker.call_args_list if f"session S{sid} " in c.args[1])
            self.assertIn(f"Your plan file is {plans[hat]}, your own", prompt)
        plans["review"].write_text("- [x] 1. Read the diff. Done when: read\n- [ ] 2. Run hw-employee finish and report (HW:DONE)\n")
        plans["qa"].write_text("- [ ] 1. Run the cases. Done when: run\n- [ ] 2. Run hw-employee finish and report (HW:DONE)\n")
        self.assertIsNone(hwlib.session_plan_problem(self.session(review), skip_finish=True))  # QA's open step is not the reviewer's
        self.assertIn("Run the cases", hwlib.session_plan_problem(self.session(qa), skip_finish=True))
        self.assertEqual(self.z.task_plan_progress(self.conn, 20), (1, 1))  # the build's alone (its PR step waits for the merge)
        self.assertEqual(_load.load("hw-plan").find_plan("T20"), build_plan)

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


PLAN = ("## Existing behaviour to protect\nNone: the repository has no tests for this code\n\n## New tests\n"
        "| Requirement | Test | Fails today because |\n|---|---|---|\n| the guard refuses a guest | test_a_guest_is_refused | no check |\n\n## Steps\n"
        "- [{0}] 1. Pin the admin path. Done when: the test passes. Tests: test_an_admin_passes. (~10 min){1}\n"
        "- [ ] 2. Refuse a guest. Done when: the test passes. Tests: test_a_guest_is_refused. (~20 min)\n"
        "- [ ] 3. Raise a draft PR against main [after-review]. Done when: it is open. (~5 min)\n"
        "- [ ] 4. Run hw-employee finish and report (HW:DONE)\n")


class FourStepTests(AssignCase):
    """plan, build, qa, review: a build of a normal task waits for a written and checked plan; a small task's build
    writes its own; a build continues from the ticks."""

    def setUp(self):
        super().setUp()
        self.plan_file = self.profile / "briefs" / "T20-api-plan.md"
        self.plan_file.parent.mkdir(exist_ok=True)
        self.size("normal")

    def size(self, size):
        self.conn.execute("UPDATE tasks SET size=? WHERE id=20", (size,))
        self.conn.commit()

    def planned(self, ticked=" ", evidence="", plan_file=None, **where):
        """A plan session that wrote the plan and finished (what `hw-employee finish` records)."""
        sid = self.spawn("plan", **where)
        (plan_file or self.plan_file).write_text(PLAN.format(ticked, evidence))
        self.z.log_event(self.conn, 20, "finish", f"S{sid} plan")
        self.finish(sid)
        return sid

    def second_repo(self):
        """A second repository on the task, with its own brief and plan file: (what `spawn` takes for it, its plan file)."""
        web, plan_file = self.root / "web", self.profile / "briefs" / "T20-web-plan.md"
        web.mkdir()
        for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                    ["commit", "-q", "--allow-empty", "-m", "init"]):
            hwlib.git(web, *cmd)
        projects = json.loads(hwlib.PROJECTS_PATH.read_text())
        projects["repos"]["web"] = {"path": str(web), "dev_base": "main"}
        hwlib.PROJECTS_PATH.write_text(json.dumps(projects))
        (self.wt_root / "web").mkdir()
        hwlib.git(web, "worktree", "add", "-q", "-b", "story/abc-20-web", str(self.wt_root / "web" / "abc-20-web"))
        brief = self.root / "brief-web.md"
        brief.write_text(BRIEF.replace("- one", f"- write `{plan_file}`"))
        return {"repo": "web", "branch": "story/abc-20-web", "brief": brief}, plan_file

    def hw(self, fn, *args, code=0):
        self.output.truncate(0)
        self.output.seek(0)
        try:
            fn(list(args))
            got = 0
        except SystemExit as e:
            got = e.code or 0
        self.assertEqual(got, code, self.output.getvalue())
        return self.output.getvalue()

    def first_prompt(self):
        return self.mock_prompt_worker.call_args.args[1]

    def test_a_plan_session_writes_the_tasks_plan_file_and_records_the_code_it_started_with(self):
        s = self.session(self.spawn("plan"))
        self.assertEqual((s["hat"], s["plan"], s["core_start"]), ("plan", str(self.plan_file), hwlib.worktree_state(self.worktree)))
        self.assertIn(f"Your plan file is {self.plan_file}: write the task's plan there as the `plan-change` skill says", self.first_prompt())
        manifest = self.z.emp.load_manifest("plan")
        self.assertEqual(manifest["deny"]["edit"], ["**", "profile/**"])  # like review and qa: nothing in the worktree
        self.assertEqual(manifest["deny"]["except"], ["profile/briefs/*-plan.md", "profile/.cache/status/{session}.log"])

    def test_a_plan_session_may_write_its_plan_and_nothing_in_the_worktree(self):
        import hwdeny
        manifest = self.z.emp.load_manifest("plan")
        def edit(path):
            return hwdeny.decide({"tool_name": "Write", "tool_input": {"file_path": str(path)}, "cwd": str(self.worktree)},
                                 manifest, self.worktree, self.profile)
        self.assertIsNone(edit(self.plan_file))
        self.assertTrue(edit(self.worktree / "guard.py"))
        self.assertTrue(edit(self.profile / "briefs" / "T20-api.md"))  # not the brief either

    def test_a_build_waits_for_a_finished_plan_and_the_coordinators_check(self):
        self.refused("build", "build waits for the plan: no plan session has finished on T20 for T20-api-plan.md: spawn one with hat=plan first")
        self.planned()
        self.refused("build", "has not been checked against the brief")
        shown = self.hw(self.z.cmd_plan, "check", "T20")
        self.assertIn("| the guard refuses a guest | test_a_guest_is_refused | no check |", shown)
        self.assertIn(f"brief {self.brief}", shown)
        self.refused("build", "has not been checked against the brief")  # reading it is not the check
        self.assertIn("recorded; a build may start on T20", self.hw(self.z.cmd_plan, "check", "T20", "ok"))
        self.assertEqual(self.session(self.spawn("build"))["hat"], "build")
        self.assertIn(f"The plan is written: {self.plan_file}. Follow it and tick it; do not write another. Start at step 1: Pin the admin path. ",
                      self.first_prompt())

    def test_a_plan_a_build_cannot_follow_is_not_checked_off(self):
        self.planned()
        self.plan_file.write_text("- [ ] 1. Do it. Done when: done (~5 min)\n")
        self.assertIn("is not one a build can follow", self.hw(self.z.cmd_plan, "check", "T20", "ok", code=1))
        self.refused("build", "has not been checked against the brief")

    def test_a_plan_check_holds_what_hw_plan_check_holds(self):
        self.planned()
        self.plan_file.write_text(PLAN.format(" ", "").replace(" Done when: the test passes. Tests: test_a_guest_is_refused. (~20 min)",
                                                               " Tests: test_a_guest_is_refused."))
        said = self.hw(self.z.cmd_plan, "check", "T20", "ok", code=1)
        self.assertIn("step 2: no 'Done when'", said)
        self.assertIn("step 2: no time estimate", said)

    def test_the_check_of_one_plan_does_not_start_the_build_on_another_brief(self):
        web, web_plan = self.second_repo()
        self.planned()
        self.hw(self.z.cmd_plan, "check", "T20", "ok")
        self.refused("build", "build waits for the plan: no plan session has finished on T20 for T20-web-plan.md", **web)
        self.planned(plan_file=web_plan, **web)  # the later plan session leaves the first plan's check standing
        self.refused("build", "the plan T20-web-plan.md of S2 has not been checked against the brief", **web)
        self.assertIn("T20 has more than one plan: say which with repo=api or repo=web", self.hw(self.z.cmd_plan, "check", "T20", code=1))
        self.assertIn("say which with repo=api or repo=web", self.hw(self.z.cmd_plan, "check", "T20", "ok", code=1))
        self.assertEqual(self.session(self.spawn("build"))["plan"], str(self.plan_file))
        self.assertIn(f"plan  {web_plan}", self.hw(self.z.cmd_plan, "check", "T20", "repo=web"))
        self.assertIn("recorded; a build may start on T20", self.hw(self.z.cmd_plan, "check", "T20", "repo=web", "ok"))
        self.assertEqual(self.session(self.spawn("build", **web))["plan"], str(web_plan))

    def test_plan_approval_is_given_for_one_plan_of_a_task_with_two(self):
        web, web_plan = self.second_repo()
        self.planned()
        self.planned(plan_file=web_plan, **web)
        (self.profile / "config" / "settings.json").write_text(json.dumps({"plan_approval": True}))
        for repo in ("api", "web"):
            self.hw(self.z.cmd_plan, "check", "T20", f"repo={repo}", "ok")
        self.assertIn("say which with repo=api or repo=web", self.hw(self.z.cmd_plan, "approve", "T20", "go", code=1))
        self.assertIn("recorded; a build may start on T20", self.hw(self.z.cmd_plan, "approve", "T20", "repo=web", "go"))
        self.refused("build", "plan approval is on and the user has not approved this plan")
        self.assertEqual(self.session(self.spawn("build", **web))["hat"], "build")

    def test_a_new_plan_needs_a_new_check(self):
        self.planned()
        self.hw(self.z.cmd_plan, "check", "T20", "ok")
        self.planned()  # planned again, e.g. after the brief changed
        self.refused("build", "has not been checked against the brief")

    def test_plan_approval_is_off_by_default_and_when_on_a_build_waits_for_the_users_words(self):
        self.planned()
        self.hw(self.z.cmd_plan, "check", "T20", "ok")
        (self.profile / "config" / "settings.json").write_text(json.dumps({"plan_approval": True}))
        self.refused("build", "plan approval is on and the user has not approved this plan")
        self.assertIn("recorded; a build may start on T20", self.hw(self.z.cmd_plan, "approve", "T20", "looks right, go"))
        self.assertIn('"looks right, go"', self.conn.execute("SELECT text FROM events WHERE kind='plan-approve'").fetchone()[0])
        self.assertEqual(self.session(self.spawn("build"))["hat"], "build")

    def test_the_users_words_may_begin_like_a_repo_argument(self):
        self.planned()
        self.hw(self.z.cmd_plan, "check", "T20", "ok")
        self.assertIn("recorded; a build may start on T20", self.hw(self.z.cmd_plan, "approve", "T20", "repo=web only, go"))
        self.assertIn('"repo=web only, go"', self.conn.execute("SELECT text FROM events WHERE kind='plan-approve'").fetchone()[0])

    def test_a_repo_argument_without_the_users_words_approves_nothing(self):
        self.planned()
        self.hw(self.z.cmd_plan, "check", "T20", "ok")
        for forgotten in (["repo=api"], ["repo=nope"], ["repo=api", "repo=api"]):  # a bare `repo=` is not the user's words
            self.assertIn(f"plan approve needs the user's words after {forgotten[-1]}", self.hw(self.z.cmd_plan, "approve", "T20", *forgotten, code=1))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM events WHERE kind='plan-approve'").fetchone())
        self.assertIn("recorded; a build may start on T20", self.hw(self.z.cmd_plan, "approve", "T20", "repo=api", "go"))

    def test_the_build_of_a_small_task_starts_without_a_plan_session_and_writes_its_own_plan(self):
        self.size("small")
        self.assertEqual(self.session(self.spawn("build"))["plan"], str(self.plan_file))
        self.assertNotIn("The plan is written", self.first_prompt())  # the brief tells it to write the plan

    def test_a_task_from_before_the_four_steps_keeps_the_flow_it_started_in(self):
        self.size(None)
        self.assertEqual(self.session(self.spawn("build"))["hat"], "build")

    def test_a_second_build_continues_from_the_first_unticked_step(self):
        self.planned(ticked="x", evidence=" Evidence: test_an_admin_passes, commit abc1234")
        self.hw(self.z.cmd_plan, "check", "T20", "ok")
        self.finish(self.spawn("build"))  # the first build stopped after step 1
        self.spawn("build")
        prompt = self.first_prompt()
        self.assertIn("1 of its 4 steps are ticked: do not redo a ticked step, its evidence is on its line. "
                      "Continue from step 2: Refuse a guest. ", prompt)
        self.assertNotIn("Pin the admin path", prompt)

    def test_a_fix_round_on_a_finished_plan_adds_its_steps_below(self):
        self.size("small")
        done = "- [x] 1. a. Done when: x (~5 min)\n- [x] Run hw-employee finish and report (HW:DONE)\n"
        self.plan_file.write_text(done)
        self.spawn("build")
        self.assertIn("Every step of it is done: add the steps of this assignment below them, numbered on from 3. ", self.first_prompt())
        self.assertEqual(hwlib.plan_lint(hwlib.plan_steps_full(done + "- [ ] 3. b. Done when: y (~5 min)\n")), [])  # the number the check wants

    def test_review_and_qa_open_together_after_the_build(self):
        self.planned(ticked="x", evidence=" Evidence: test_an_admin_passes")
        self.hw(self.z.cmd_plan, "check", "T20", "ok")
        build = self.spawn("build")
        self.refused("qa", "qa waits until the build is complete")
        self.plan_file.write_text(self.plan_file.read_text().replace("- [ ] 2.", "- [x] 2.").replace("- [ ] 4.", "- [x] 4."))
        self.finish(build)
        self.assertEqual([self.session(self.spawn(hat))["status"] for hat in ("qa", "review")], ["running", "running"])

    def test_review_and_qa_wait_for_a_build_on_a_normal_task(self):
        for hat in ("qa", "review"):
            self.refused(hat, f"{hat} waits until the build is complete: no build has run on T20")
        self.planned()  # a written plan is not a build
        self.hw(self.z.cmd_plan, "check", "T20", "ok")
        self.refused("qa", "qa waits until the build is complete: no build has run on T20")
        self.size(None)  # a task from before the four steps: as it was
        self.assertEqual(self.session(self.spawn("qa"))["hat"], "qa")

    def test_review_and_qa_of_a_small_task_wait_for_its_build(self):
        self.size("small")
        for hat in ("qa", "review"):
            self.refused(hat, f"{hat} waits until the build is complete: no build has run on T20")
        self.plan_file.write_text("- [x] 1. a. Done when: x\n")
        self.finish(self.spawn("build"))
        self.assertEqual([self.session(self.spawn(hat))["hat"] for hat in ("qa", "review")], ["qa", "review"])

    def test_the_first_prompt_names_a_step_without_the_dash_before_its_done_when(self):
        self.size("small")
        self.plan_file.write_text("- [ ] 1. Add the greeting \u2014 done when the test passes. Tests: test_greets. ~10m\n")
        self.spawn("build")
        self.assertIn("Start at step 1: Add the greeting. ", self.first_prompt())

    def test_status_names_a_plan_sessions_plan_once(self):
        sid = self.spawn("plan")
        self.plan_file.write_text(PLAN.format(" ", ""))
        status = self.z.render_status(self.conn, live={})
        self.assertIn(f"- S{sid} plan `T20-api-plan.md`: 0/", status)
        self.assertNotIn("plan plan", status)  # a review's stays "S2 review plan `…`" (WatcherAndStartTests)

    def test_a_task_is_normal_unless_it_is_marked_small(self):
        self.assertEqual(self.hw(self.z.cmd_task, "add", "title=Next").strip(), "T21")
        self.assertEqual(self.conn.execute("SELECT size FROM tasks WHERE id=21").fetchone()[0], "normal")
        self.hw(self.z.cmd_task, "add", "title=Typo", "size=small")
        self.hw(self.z.cmd_task, "set", "T21", "size=small")
        self.assertEqual([r[0] for r in self.conn.execute("SELECT size FROM tasks WHERE id>20 ORDER BY id")], ["small", "small"])
        self.assertIn("size is normal or small, not 'tiny'", self.hw(self.z.cmd_task, "set", "T21", "size=tiny", code=1))

    def test_a_plan_sessions_done_tells_the_coordinator_to_check_the_plan(self):
        sid = self.planned()
        done = {"kind": "DONE", "items": [], "text": "x", "fields": {"Summary": "planned it", "PR": "none", "QA": "hw-plan check passes"}}
        text = self.z.record_block(self.conn, self.session(sid), done)
        self.assertIn("Plan written: check it against the brief before the build (`bin/hw plan check T20`)", text)
        self.assertNotIn("incomplete plan", text)


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
        self.spawn("review")
        (self.profile / "briefs" / "T20-review-S2-plan.md").write_text(
            "- [x] 1. Read the diff\n- [ ] 2. Run the suite\n- [ ] 3. Run hw-employee finish and report (HW:DONE)\n")
        digest = "\n".join(self.z.render_brief(self.conn, live={}))
        self.assertIn("S2 T20 review-t20 running/GONE · review 1/3", digest)  # not the build plan's 2/2
        status = self.z.render_status(self.conn, live={})
        self.assertIn("plan `T20-api-plan.md`: 2/2 steps done (+1 awaiting merge/PR)", status)  # the task shows its build plan only
        self.assertIn("S2 review plan `T20-review-S2-plan.md`: 1/3 steps done", status)
        self.assertNotIn("plan `T20-review-S2-plan.md`", status.replace("S2 review plan `T20-review-S2-plan.md`", ""))

    def test_start_reports_the_crew_but_no_employee_board(self):
        labels = []
        with patch.object(self.z, "holds_lock", return_value=True), patch.object(self.z, "cmd_lock"), \
                patch.object(self.z, "live_agents", return_value={}), patch.object(self.z, "watcher_alive", return_value=None), \
                patch.object(self.z, "queue_pending", return_value=[]), \
                patch.object(self.z, "script_findings", side_effect=lambda label, *a, **k: labels.append(label) or []):
            self.z.cmd_start([])
        self.assertEqual(labels, ["sessions out of step with the DB (hw-crew)", "core commits not on main yet (hw-core-sync)",
                                  "git guard (hw-guard)"])


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
            asked = body.get("model") and {"variant": "default"} | body["model"]  # no variant asked for: OpenCode names its own
            return {"id": "ses_test1"} | ({"model": self.model or asked} if self.model or asked else {})
        if path.endswith("/prompt"):
            return {"id": f"msg_{len(self.calls)}"}
        if path.endswith("/message"):
            command = re.search(r"hw-(deny|pretool)-canary \w+", self.prompts()[0]).group(0)
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
                                   "model": {"providerID": "acme", "id": "base", "variant": "xhigh"}})
        start = self.start_call()
        self.assertEqual((start[start.index("--kind") + 1], start[start.index("--timeout") + 1]), ("opencode", "180000"))
        self.assertEqual(self.after_dashes(start), ["--server", url, "--auto", "--session", "ses_test1"])  # no --model: the TUI has none
        s = self.session(sid)
        self.assertEqual((s["status"], s["claude_session_id"], s["model"], s["effort"]),
                         ("running", "ses_test1", "acme/base", "xhigh"))
        event = self.conn.execute("SELECT text FROM events WHERE kind='spawn'").fetchone()[0]
        self.assertIn("model acme/base variant xhigh (opencode.json)", event)
        self.assertIn("runtime opencode, session ses_test1", event)
        self.assertFalse((self.kit_dir.parent / "review-t20-settings.json").exists())
        self.mock_require_capacity.assert_called_once()  # opencode.json's max_running counts OpenCode sessions

    def test_model_and_variant_come_from_the_profiles_opencode_json_and_model_can_be_given(self):
        self.refused("review", "an OpenCode model is written provider/model, not 'nemotron'", runtime="opencode", model="nemotron")
        (self.profile / "config" / "opencode.json").write_text(json.dumps({"model": "acme/m-1", "variant": "low"}))
        self.spawn("review", runtime="opencode")
        self.assertEqual(self.server.calls[1][2]["model"], {"providerID": "acme", "id": "m-1", "variant": "low"})
        (self.profile / "config" / "opencode.json").write_text(json.dumps({"variant": "high"}))
        self.refused("qa", "no model is set for OpenCode", runtime="opencode")  # a variant alone names no model
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
        self.refused("qa", "'minimal' is not an effort of Claude Code: low, medium, high, xhigh, max", effort="minimal")  # its own list applies

    def test_without_a_model_an_opencode_worker_is_refused_because_its_server_would_pick_one(self):
        (self.profile / "config" / "opencode.json").unlink()
        self.refused("review", "no model is set for OpenCode (config/opencode.json), and a session created without one runs whatever "
                               "OpenCode's server picks, not the user's choice. Ask the user which model to use, then "
                               "`bin/hw configure --model <provider/model>`", runtime="opencode")
        self.assertEqual(self.server.calls, [])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sessions").fetchone())

    def test_a_model_without_a_variant_is_passed_alone(self):
        (self.profile / "config" / "opencode.json").write_text(json.dumps({"model": "acme/m-1"}))
        sid = self.spawn("review", runtime="opencode")
        self.assertEqual(self.server.calls[1][2]["model"], {"providerID": "acme", "id": "m-1"})
        self.assertEqual((self.session(sid)["status"], self.session(sid)["effort"]), ("running", None))

    def test_the_canary_is_denied_before_the_tui_starts_and_the_brief_goes_through_the_server(self):
        sid = self.spawn("review", runtime="opencode")
        canary, brief = self.server.prompts()
        self.assertRegex(canary, r"exactly this command: hw-deny-canary [0-9a-f]{8}\n")
        order = [c[:3] for c in self.herdr_calls]
        self.assertLess(order.index(["api", "GET", "/session/ses_test1/message"]), order.index(["herdr", "agent", "start"]))
        self.assertIn(["api", "POST", "/experimental/session/ses_test1/wait"], order)  # the canary's turn has ended
        self.assertIn(f"You are the engineering worker, NOT the Herdwright coordinator, for session S{sid}", brief)
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
        """A usage limit showed only as "the model made no canary call"."""
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
        self.assertIn("# You are a Herdwright worker", rules)
        self.assertIn("# Hat: review", rules)
        self.assertIn("## You run on OpenCode", rules)
        self.assertIn("does not read `CLAUDE.md`", rules)
        self.assertTrue(rules.startswith((self.kit_dir.parent / "review-t20-rules.md").read_text()))
        self.assertEqual((self.worktree / "AGENTS.md").read_text(), "# The repository's own instructions\n")
        self.assertEqual(hwlib.git(self.worktree, "status", "--porcelain").stdout.strip(), "?? AGENTS.md")  # nothing else written
        plugin = (self.kit_dir / "plugins" / "hw-deny" / "index.js").read_text()
        self.assertIn('"HW_HAT": "review"', plugin)
        self.assertIn(f'"HW_WORKTREE": "{self.worktree}"', plugin)
        self.assertIn('const DENY_TOOLS = ["tracker_createIssue"];', plugin)

    def test_the_opencode_plugin_of_a_project_without_worktrees_holds_the_scratch_folder(self):
        """On OpenCode the guard runs from the plugin, with the plugin's environment: HW_SCRATCH has to be in it."""
        (self.profile / "config" / "projects.json").write_text(json.dumps({
            "worktree_root": str(self.wt_root), "repos": {"api": {"path": str(self.repo), "dev_base": "main", "worktrees": False}}}))
        self.spawn("qa", runtime="opencode")
        plugin = (self.kit_dir.parent / "qa-t20-opencode" / "plugins" / "hw-deny" / "index.js").read_text()
        self.assertIn(f'"HW_SCRATCH": "{self.repo / "T20-qa-t20"}"', plugin)
        self.assertIn(f'"HW_WORKTREE": "{self.repo}"', plugin)

    def test_session_identity_follows_opencode_env_and_a_claude_start_arms_no_watcher(self):
        with patch.dict(os.environ, {"OPENCODE_SESSION_ID": "oc-1", "OPENCODE_PID": "4242"}, clear=False):
            for k in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_PID"):
                os.environ.pop(k, None)
            with patch.object(self.z, "pane_session", return_value=None):
                self.assertEqual((self.z.me()["agent_session"], self.z.me()["pid"]), ("oc-1", 4242))
        self.conn.execute("INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,status,created_at,updated_at) "
                          "VALUES (20,'api','main','b','/w','a','running','x','x')")
        self.conn.commit()
        armed = []  # a Claude Herdwright relies on its Stop hooks: start does not arm (an OpenCode one: test_hw_coordinator)
        with patch.object(self.z, "holds_lock", return_value=True), patch.object(self.z, "cmd_lock"), \
                patch.object(self.z, "live_agents", return_value={}), patch.object(self.z, "queue_pending", return_value=[]), \
                patch.object(self.z, "script_findings", return_value=[]), \
                patch.object(self.z, "watcher_alive", return_value=None), \
                patch.object(self.z, "arm_watcher", side_effect=lambda: armed.append(1)):
            self.z.cmd_start([])
        self.assertEqual(armed, [])
        self.assertIn("watcher: NOT RUNNING — arm it: bin/hw watch --once (background)", self.output.getvalue())


class RenderedPolicyTests(AssignCase):
    def kit(self, hat):
        return self.z.hat_kit(hat, 7, "review-t20", self.worktree)

    def test_claude_gets_a_settings_hook_with_the_deny_and_stop_guard(self):
        kit, env = self.kit("review")
        claude = self.z.claude_launch("review-t20", None, None, kit)
        self.assertEqual(claude[claude.index("--settings") + 1], str(kit["claude_settings"]))
        self.assertEqual(claude[claude.index("--append-system-prompt-file") + 1], str(kit["rules"]))
        settings = json.loads(kit["claude_settings"].read_text())
        self.assertEqual(settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"], str(HOME / "bin" / "hw-deny"))
        self.assertEqual(settings["hooks"]["Stop"][0]["hooks"][0]["command"], f"{HOME / 'bin' / 'hw-employee'} stop-guard")
        self.assertIn("HW_HAT=review", env)
        self.assertEqual(settings["permissions"]["deny"], [])  # no profile setting: nothing is denied by name

    def test_claude_settings_deny_the_tools_the_profile_names(self):
        tools = ["mcp__tracker__createIssue", "mcp__wiki__updatePage"]
        (self.profile / "config" / "settings.json").write_text(json.dumps({"worker": {"deny_tools": tools}}))
        kit, _ = self.kit("build")
        self.assertEqual(json.loads(kit["claude_settings"].read_text())["permissions"]["deny"], tools)


if __name__ == "__main__":
    unittest.main()
