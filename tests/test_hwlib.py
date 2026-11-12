import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import _load  # noqa: F401  (puts bin/lib on sys.path)
import hwlib


class HwlibTests(unittest.TestCase):
    def test_run_bounded_ok_missing_and_timeout(self):
        self.assertTrue(hwlib.run_bounded(["echo", "hi"]).ok)
        self.assertEqual(hwlib.run_bounded(["no-such-binary-hw"]).returncode, 127)
        r = hwlib.run_bounded([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)
        self.assertTrue(r.timed_out)
        self.assertFalse(r.ok)

    def test_cap_lines(self):
        out = hwlib.cap_lines([f"line {i}" for i in range(5)], limit=3)
        self.assertEqual(out, ["line 0", "line 1", "line 2", "... 2 more omitted"])
        self.assertEqual(hwlib.cap_lines(["x" * 50], width=10), ["x" * 9 + "…"])

    def test_parse_plan_steps(self):
        steps = hwlib.parse_plan_steps("- [x] 1. a\n- [ ] 2. b\nnot a step\n  - [X] 3. c\n")
        self.assertEqual([(s["n"], s["done"]) for s in steps], [(1, True), (2, False), (3, True)])


class PlanGateTests(unittest.TestCase):
    def steps(self, *lines):
        return hwlib.parse_plan_steps("\n".join(lines))

    def test_merge_push_and_pr_steps_are_exempt_and_others_are_not(self):
        for title in ("Raise the draft PR. Done when: url", "Merge the branch after review", "Push the branch", "Open a pull request",
                      "Create PR", "Push to origin (no force)", "Push main to origin ff-only", "Push `120baef:main` from the worktree",
                      "Raise a draft PR against main", "Open draft PR vs main with what/why", "Open the PR \u2014 then wait", "Wait for CI [after-review]",
                      "Merge profile after review [AFTER-REVIEW]"):
            self.assertTrue(hwlib.step_exempt(self.steps(f"- [ ] 1. {title}")[0]), title)

    def test_a_bare_merge_push_or_pr_word_never_exempts_a_step(self):
        for title in ("Review the PR comments. Done when: x", "Fix the parser", "Write tests. Done when: merge conflict gone",
                      "Add PR template", "Implement merge logic for duplicate rows", "Push notification handler",
                      "Fix the push-to-deploy hook", "Create tests for the PR template parser", "Open the pull request list page",
                      "File a PR comment parser", "Merge profile/example and run the suite", "Fix it, then push",
                      "Merge the changes handler", "Merge work items list", "Push the branch handler refactor"):
            self.assertFalse(hwlib.step_exempt(self.steps(f"- [ ] 1. {title}")[0]), title)

    def test_a_marker_after_a_long_title_still_exempts_while_the_label_stays_short(self):
        title = ("Independent review and QA of the head with registered verdicts, then push the branch and read back the "
                 "remote head [after-review] done when: the remote matches")
        step = self.steps(f"- [ ] 7. {title}")[0]
        self.assertTrue(hwlib.step_exempt(step))
        self.assertLessEqual(len(hwlib.step_label(step)), 60)
        long_ordinary = self.steps("- [ ] 3. " + "Rework the parser " * 5 + "and push notification handler [after] review")[0]
        self.assertFalse(hwlib.step_exempt(long_ordinary))

    def test_a_long_plan_with_a_late_marker_no_longer_blocks_review(self):
        blocking, awaiting = hwlib.plan_open(self.steps("- [x] 1. Build it", "- [ ] 2. " + "Verify and then push to origin " * 4 + "[after-review]",
                                                          "- [ ] 3. Fix the docs"))
        self.assertEqual([s["n"] for s in blocking], [3])
        self.assertEqual([s["n"] for s in awaiting], [2])

    def test_the_real_plans_of_this_profile_classify_sensibly(self):
        """Pinned against today's plan files: of ~600 steps only genuine push / PR steps are exempt."""
        exempt = {"Push branch to origin (no force)", "Push main to origin ff-only (est 2m)"}
        for title in exempt:
            self.assertTrue(hwlib.step_exempt(self.steps(f"- [ ] 1. {title}")[0]), title)
        for title in ("Re-run M1-M3, all tests, commit (no push), hw-employee finish", "Commit (no push), hw-employee finish",
                      "Merge profile/example (keep both COMMANDS entries) and run full suite", "Merge hook: refuses unvetted merge, accepts vetted (~10m)",
                      "Push/PR gate: gh/hw-pr and `git push` in a worker worktree"):
            self.assertFalse(hwlib.step_exempt(self.steps(f"- [ ] 1. {title}")[0]), title)

    def test_open_steps_split_into_blocking_and_exempt(self):
        blocking, exempt = hwlib.plan_open(self.steps("- [x] 1. a. Done when: x", "- [ ] 2. b. Done when: y",
                                                       "- [ ] 3. Raise PR. Done when: url"))
        self.assertEqual([s["n"] for s in blocking], [2])
        self.assertEqual([s["n"] for s in exempt], [3])

    def test_a_catch_all_tick_counts_as_open(self):
        for line in ("- [x] 1-5 done", "- [x] Steps 2 to 4 done", "- [x] All done", "- [x] 1 - 3 complete", "- [x] 1. 1-5 done",
                     "- [x] 2. All done"):
            self.assertTrue(hwlib.step_catch_all(self.steps(line)[0]), line)
        self.assertEqual(len(hwlib.plan_open(self.steps("- [x] 1-5 done"))[0]), 1)
        self.assertFalse(hwlib.step_catch_all(self.steps("- [x] 1. Fix 5 files")[0]))


class SessionPlanTests(unittest.TestCase):
    """Which plan a session, a task's progress and the gate read."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("CREATE TABLE sessions (id INTEGER PRIMARY KEY, task_id INTEGER, external INTEGER DEFAULT 0, hat TEXT, plan TEXT)")

    def session(self, hat, plan, text, external=0):
        path = self.dir / plan
        path.write_text(text)
        self.conn.execute("INSERT INTO sessions(task_id, external, hat, plan) VALUES (7, ?, ?, ?)", (external, hat, str(path)))

    def test_brief_names_the_plan_in_its_plan_section(self):
        brief = "## Context\n- see `x-plan.md`\n\n## Plan\n- Before any work, write `/p/briefs/T7-fixes-plan.md`: a checklist\n\n## Definition of done\n"
        self.assertEqual(hwlib.brief_plan_file(brief), Path("/p/briefs/T7-fixes-plan.md"))
        self.assertIsNone(hwlib.brief_plan_file("## Plan\n- write it somewhere\n"))

    def test_task_progress_and_the_gate_read_build_and_fix_plans_only(self):
        self.session("build", "T7-plan.md", "- [x] 1. a\n- [x] 2. b\n")
        self.session("review", "T7-review-plan.md", "- [ ] 1. Read the diff\n")
        self.session("build", "T7-fixes-plan.md", "- [x] 1. fix\n- [ ] 2. more\n")
        self.session("qa", "T7-qa-plan.md", "- [ ] 1. Browse\n")
        self.session(None, "T7-ext-plan.md", "- [x] 1. plain worker\n", external=1)
        self.assertEqual([p.name for p in hwlib.task_plans(self.conn, 7)], ["T7-plan.md", "T7-fixes-plan.md"])
        self.assertEqual(hwlib.gate_plan(self.conn, 7).name, "T7-fixes-plan.md")  # the latest build/fix, not a review plan
        self.assertIn("step 2: more", hwlib.plan_gate_problem(self.conn, 7))

    def test_a_review_plan_never_holds_the_gate_and_a_task_without_a_build_plan_is_not_gated(self):
        self.session("review", "T7-review-plan.md", "- [ ] 1. Run finish\n")
        self.assertIsNone(hwlib.plan_gate_problem(self.conn, 7))
        self.session("build", "T7-plan.md", "- [x] 1. a\n")
        self.assertIsNone(hwlib.plan_gate_problem(self.conn, 7))

    def test_finish_step_is_exactly_the_templates_final_line(self):
        for line in ("- [ ] Run hw-employee finish and report (HW:DONE)", "- [ ] 6. Run `hw-employee finish` and report (HW:DONE) \u2014 done when HW:DONE is sent",
                     "- [ ] 6. run hw-employee finish and report (HW:DONE). ~5 min"):
            steps = hwlib.parse_plan_steps(f"- [x] 1. a\n{line}\n")
            self.assertEqual(hwlib.finish_step(steps)["line"], line, line)

    def test_other_wordings_are_ordinary_steps_not_finish_steps(self):
        for title in ("Fix the report generator", "Report page: add export", "Finish the migration script", "Write the report of failures",
                      "Finish the report page", "Run finish", "Run hw-employee finish", "`hw-employee finish`, HW:DONE",
                      "Run hw-employee finish and report (HW:DONE) for every repo"):
            self.assertIsNone(hwlib.finish_step(hwlib.parse_plan_steps(f"- [ ] 1. a\n- [ ] 2. {title}\n")), title)
        self.assertIsNone(hwlib.finish_step([]))  # a plan with no steps
        self.assertIsNone(hwlib.finish_step(hwlib.parse_plan_steps("- [x] 1. Run hw-employee finish and report (HW:DONE)\n")))  # ticked: nothing to tick
        self.assertIsNone(hwlib.finish_step(hwlib.parse_plan_steps("- [ ] 1. Run hw-employee finish and report (HW:DONE)\n- [ ] 2. Fix it\n")))  # not last


if __name__ == "__main__":
    unittest.main()
