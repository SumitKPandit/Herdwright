import subprocess
import tempfile
import unittest
from pathlib import Path

import _load

plan = _load.load("hw-plan")
hwlib = plan.hwlib
GOOD = """# plan
- [x] 1. Triage. Done when: all rows. (~40 min)
- [ ] 2. Build lib
  Done when: tests pass. (~1 h)
- [ ] 3. Report. Done when: sent (~20 min)

## Notes
"""


TEST_FIRST = """# Plan: T1 greeting
## Existing behaviour to protect
| Behaviour that must stay | Covered by | Test to add (passes today) |
|---|---|---|
| hello() returns a string | no test | test_hello_returns_a_string |

## New tests
| Requirement of the brief | Test | Fails today because |
|---|---|---|
| greets by name | test_greets_by_name | hello() takes no name |

## Steps
- [x] 1. Add the protecting test. Done when: it passes. Tests: test_hello_returns_a_string. (~10 min) Evidence: test_hello_returns_a_string
- [ ] 2. Greet by name
  Done when: the new test passes. Tests: test_greets_by_name. (~20 min)
- [ ] 3. Raise a draft PR against main [after-review]. Done when: open (~5 min)
- [ ] Run hw-employee finish and report (HW:DONE)
"""


class CheckTests(unittest.TestCase):
    """`hw-plan check`: what a plan must hold before a build may follow it."""

    def problems(self, text):
        return " | ".join(plan.hwlib.plan_check_problems(text))

    def test_a_test_first_plan_passes(self):
        self.assertEqual(plan.hwlib.plan_check_problems(TEST_FIRST), [])
        self.assertEqual(hwlib.plan_lint(plan.parse(TEST_FIRST)), [])

    def test_a_plan_without_the_behaviour_to_protect_or_the_new_tests_is_refused(self):
        for title in plan.hwlib.PLAN_TEST_SECTIONS:
            self.assertIn(f"no section '## {title}'", self.problems(TEST_FIRST.replace(f"## {title}", "## Other")), title)
        empty = TEST_FIRST.replace("| greets by name | test_greets_by_name | hello() takes no name |\n", "")
        self.assertIn("section '## New tests' is empty", self.problems(empty))  # a table's header alone lists nothing
        template = TEST_FIRST.replace("| hello() returns a string | no test | test_hello_returns_a_string |", "<Every behaviour the change touches.>")
        self.assertIn("section '## Existing behaviour to protect' is empty", self.problems(template))

    def test_every_step_names_its_tests_but_the_pr_step_and_the_finish_line(self):
        self.assertEqual(self.problems(TEST_FIRST.replace(" Tests: test_greets_by_name.", "")),
                         "step 2: names no tests ('Tests: <names>', or 'Tests: none' where the repository has none)")

    def test_a_repository_without_tests_says_so(self):
        text = TEST_FIRST
        for row in ("| hello() returns a string | no test | test_hello_returns_a_string |", "| greets by name | test_greets_by_name | hello() takes no name |"):
            text = text.replace(row, "None: the repository has no tests for this code")
        text = text.replace("Tests: test_hello_returns_a_string.", "Tests: none.").replace("Tests: test_greets_by_name.", "Tests: none.")
        self.assertEqual(plan.hwlib.plan_check_problems(text), [])

    def test_a_tick_says_where_its_evidence_is(self):
        self.assertEqual(self.problems(TEST_FIRST.replace(" Evidence: test_hello_returns_a_string", "")),
                         "step 1: ticked without 'Evidence: <test name or commit>'")

    def test_a_plan_check_holds_what_lint_holds(self):
        bare = TEST_FIRST.replace("\n  Done when: the new test passes. Tests: test_greets_by_name. (~20 min)", " Tests: test_greets_by_name.")
        self.assertEqual(self.problems(bare), "step 2: no 'Done when' | step 2: no time estimate (like '~40 min')")
        self.assertEqual(plan.hwlib.plan_check_problems("# nothing yet\n").count("no checklist steps found ('- [ ] 1. ...')"), 1)

    def test_cli_check_names_a_plan_without_steps_once(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "T1-x-plan.md"
            f.write_text("# nothing yet\n")
            r = subprocess.run([str(_load.HOME / "bin" / "hw-plan"), "check", str(f)], capture_output=True, text=True)
            self.assertEqual(r.stdout.count("no checklist steps found"), 1, r.stdout)

    def test_cli_check_adds_the_sections_to_lint(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "T1-x-plan.md"
            for text, code, said in ((TEST_FIRST, 0, "T1-x-plan.md: ok"), (GOOD, 1, "no section '## New tests'")):
                f.write_text(text)
                r = subprocess.run([str(_load.HOME / "bin" / "hw-plan"), "check", str(f)], capture_output=True, text=True)
                self.assertEqual(r.returncode, code, r.stdout)
                self.assertIn(said, r.stdout)
            self.assertEqual(subprocess.run([str(_load.HOME / "bin" / "hw-plan"), "lint", str(f)], capture_output=True).returncode, 0)


class PlanTests(unittest.TestCase):
    def test_estimates(self):
        self.assertEqual(hwlib.estimate_minutes("x (~40 min)"), 40)
        self.assertEqual(hwlib.estimate_minutes("~1.5 h"), 90)
        self.assertIsNone(hwlib.estimate_minutes("no estimate"))
        for text, minutes in (("x (2 min)", 2), ("x, 45 minutes", 45), ("x (1 h)", 60), ("x ~ 10 mins", 10)):
            self.assertEqual(hwlib.estimate_minutes(text), minutes, text)  # the brief asks for "a rough time", no "~"
        self.assertIsNone(hwlib.estimate_minutes("x in v2 hosts"))
        for text, minutes in (  # another time earlier in the step is not its estimate
                ("3. Doorbell: an unanswered ask >30 min becomes an event (fake clock) (~45 min) — done when tested", 45),
                ("3. Producer: refresh every 15 min in a background thread. Done when: in place (~30 min)", 30),
                ("4. Cache for 24 h, then refetch. Done when: tested (~45 min)", 45),
                ("5. Retry after 10 min. Done when: tested (20 min)", 20)):
            self.assertEqual(hwlib.estimate_minutes(text), minutes, text)

    def test_lint_clean_and_multiline_step(self):
        steps = plan.parse(GOOD)
        self.assertEqual(hwlib.plan_lint(steps), [])
        self.assertEqual(steps[1]["est_min"], 60)

    def test_lint_problems(self):
        steps = plan.parse("- [ ] 1. a (~5 min)\n- [ ] 1. b. Done when: x\n- [ ] 4. c\n")
        text = "\n".join(hwlib.plan_lint(steps))
        for needle in ("step 1: no 'Done when'", "duplicate number", "step 1: no time estimate", "out of order (expected 3)"):
            self.assertIn(needle, text)
        self.assertEqual(hwlib.plan_lint(plan.parse("- [ ] 0. a. Done when: x (~5 min)\n- [ ] 1. b. Done when: x (~5 min)\n")), [])
        self.assertEqual(hwlib.plan_lint([]), ["no checklist steps found ('- [ ] 1. ...')"])

    def test_lint_accepts_the_briefs_fixed_finish_line_as_the_last_step(self):
        line = next(l.strip().strip("`") for l in (_load.HOME / "templates" / "brief.md").read_text().split("`")
                    if l.startswith("- [ ] Run hw-employee finish"))  # the line the brief template prescribes
        self.assertEqual(hwlib.plan_lint(plan.parse(f"- [ ] 1. a. Done when: x (~5 min)\n{line}\n")), [])
        self.assertEqual(hwlib.plan_lint(plan.parse(f"- [x] 1. a. Done when: x (~5 min)\n{line.replace('[ ]', '[x]')}\n")), [])
        # anywhere but last it is an ordinary step, and so is any other wording
        self.assertIn("step 1: no 'Done when'", "\n".join(hwlib.plan_lint(plan.parse(f"{line}\n- [ ] 2. a. Done when: x (~5 min)\n"))))
        self.assertIn("step 2: no time estimate", "\n".join(hwlib.plan_lint(plan.parse("- [ ] 1. a. Done when: x (~5 min)\n- [ ] 2. Run the finish\n"))))

    def test_lint_accepts_the_ticked_finish_line_of_an_earlier_round(self):
        """A fix round adds its steps below a finished plan: the line `finish` ticked then is no longer the last."""
        line = "- [x] Run hw-employee finish and report (HW:DONE)"
        text = f"- [x] 1. a. Done when: x (~5 min)\n{line}\n- [ ] 3. Fix it. Done when: y (~5 min)\n{line.replace('[x]', '[ ]')}\n"
        self.assertEqual(hwlib.plan_lint(plan.parse(text)), [])

    def test_lint_rejects_a_catch_all_tick(self):
        steps = plan.parse("- [x] 1-5 done. Done when: all (~5 min)\n- [x] 6. b. Done when: x (~5 min)\n")
        self.assertIn("catch-all tick", "\n".join(hwlib.plan_lint(steps)))
        self.assertEqual(hwlib.plan_lint(plan.parse("- [x] 1. one. Done when: x (~5 min)\n- [x] 2. two. Done when: y (~5 min)\n")), [])

    def test_progress_counts_merge_and_pr_steps_apart(self):
        steps = plan.parse("- [x] 1. a. Done when: x (~5 min)\n- [ ] 2. Raise PR. Done when: url (~5 min)\n")
        p = plan.progress(steps, 1000.0, now=1000.0)
        self.assertEqual((p["done"], p["total"], p["awaiting"], p["next"]), (1, 1, 1, None))

    def test_progress_and_overrun(self):
        steps = plan.parse(GOOD)
        p = plan.progress(steps, mtime := 1000.0, now=mtime + 30 * 60)
        self.assertEqual((p["done"], p["total"], p["remaining_est_min"], p["overrun"]), (1, 3, 80, False))
        p = plan.progress(steps, mtime, now=mtime + 121 * 60)  # step 2 est 60 → overrun past 120 min
        self.assertTrue(p["overrun"])
        self.assertIsNone(plan.progress(plan.parse("- [x] 1. a (~5 min)"), 0)["next"])

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "T1-x-plan.md"
            f.write_text(GOOD)
            r = subprocess.run([str(_load.HOME / "bin" / "hw-plan"), "lint", str(f)], capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "T1-x-plan.md: ok"))
            r = subprocess.run([str(_load.HOME / "bin" / "hw-plan"), "progress", str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn("1/3 done, ~80 min", r.stdout)
            self.assertEqual(subprocess.run([str(_load.HOME / "bin" / "hw-plan"), "lint", d + "/nope"], capture_output=True).returncode, 2)


DESIGN = """# Design: widget export

## Context
Some prose.

## Build units

### Unit A: Export endpoint
- Scope: add GET /export returning CSV
  for the current filters
- Files: api/export.py, api/urls.py
- Done when: curl returns the CSV; tests green
- Estimate: ~2 h
- Depends on: none

### Unit B: Export button
- Scope: button in the toolbar calling the endpoint
- Files: web/Toolbar.tsx
- Done when: button downloads the file in a browser
- Estimate: ~90 min
- Depends on: A

### Unit C: Docs
- Scope: document the endpoint
- Files: api/urls.py, docs/export.md
- Done when: docs page merged
- Estimate: ~30 min
- Depends on: none

## Risks
- not a unit
"""


class SplitTests(unittest.TestCase):
    def test_units_parse_with_dependencies_and_continuations(self):
        units = plan.parse_units(DESIGN)
        self.assertEqual([u["id"] for u in units], ["A", "B", "C"])
        self.assertEqual(units[0]["scope"], "add GET /export returning CSV for the current filters")
        self.assertEqual((units[0]["files"], units[0]["est_min"], units[0]["deps"]), (["api/export.py", "api/urls.py"], 120, []))
        self.assertEqual((units[1]["deps"], units[1]["est_min"]), (["A"], 90))
        self.assertNotIn("not a unit", str(units[2]["_fields"]))

    def test_waves_and_parallel_file_warning(self):
        problems, warnings, waves = plan.check_units(plan.parse_units(DESIGN))
        self.assertEqual(problems, [])
        self.assertEqual(waves, {"A": 0, "B": 1, "C": 0})
        self.assertEqual(warnings, ["units A and C can run at the same time but both touch api/urls.py"])

    def test_problems(self):
        bad = DESIGN.replace("- Depends on: A", "- Depends on: Z").replace("- Done when: docs page merged\n", "")
        problems = "\n".join(plan.check_units(plan.parse_units(bad))[0])
        self.assertIn("unit B: depends on unknown unit 'Z'", problems)
        self.assertIn("unit C: no 'Done when'", problems)
        self.assertIn("duplicate id", "\n".join(plan.check_units(plan.parse_units(DESIGN + "\n### Unit A: again\n"))[0]))
        cyc = DESIGN.replace("Estimate: ~2 h\n- Depends on: none", "Estimate: ~2 h\n- Depends on: B")
        self.assertIn("dependency cycle: A -> B -> A", plan.check_units(plan.parse_units(cyc))[0][0])
        self.assertIn("no units found", plan.check_units([])[0][0])
        self.assertIn("estimate needs", "\n".join(plan.check_units(plan.parse_units(DESIGN.replace("~90 min", "soon")))[0]))

    def test_cli_split_writes_skeletons_only_when_clean(self):
        with tempfile.TemporaryDirectory() as d:
            f, out = Path(d) / "design.md", Path(d) / "briefs"
            f.write_text(DESIGN)
            r = subprocess.run([str(_load.HOME / "bin" / "hw-plan"), "split", str(f), "--out", str(out)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("wave 1  B: Export button (~90 min)  after A", r.stdout)
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["A-export-endpoint.md", "B-export-button.md", "C-docs.md"])
            text = (out / "B-export-button.md").read_text()
            self.assertIn("Depends on: unit A", text)
            self.assertIn("button downloads the file in a browser", text)
            f.write_text(DESIGN.replace("- Depends on: A", "- Depends on: Z"))
            out2 = Path(d) / "none"
            r = subprocess.run([str(_load.HOME / "bin" / "hw-plan"), "split", str(f), "--out", str(out2)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertFalse(out2.exists())
            self.assertEqual(subprocess.run([str(_load.HOME / "bin" / "hw-plan"), "split", d + "/nope"], capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
