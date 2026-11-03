import subprocess
import tempfile
import unittest
from pathlib import Path

import _load

plan = _load.load("zed-plan")
GOOD = """# plan
- [x] 1. Triage. Done when: all rows. (~40 min)
- [ ] 2. Build lib
  Done when: tests pass. (~1 h)
- [ ] 3. Report. Done when: sent (~20 min)

## Notes
"""


class PlanTests(unittest.TestCase):
    def test_estimates(self):
        self.assertEqual(plan.estimate_minutes("x (~40 min)"), 40)
        self.assertEqual(plan.estimate_minutes("~1.5 h"), 90)
        self.assertIsNone(plan.estimate_minutes("no estimate"))

    def test_lint_clean_and_multiline_step(self):
        steps = plan.parse(GOOD)
        self.assertEqual(plan.lint(steps), [])
        self.assertEqual(steps[1]["est_min"], 60)

    def test_lint_problems(self):
        steps = plan.parse("- [ ] 1. a (~5 min)\n- [ ] 1. b. Done when: x\n- [ ] 4. c\n")
        text = "\n".join(plan.lint(steps))
        for needle in ("step 1: no 'Done when'", "duplicate number", "step 1: no time estimate", "out of order (expected 3)"):
            self.assertIn(needle, text)
        self.assertEqual(plan.lint(plan.parse("- [ ] 0. a. Done when: x (~5 min)\n- [ ] 1. b. Done when: x (~5 min)\n")), [])
        self.assertEqual(plan.lint([]), ["no checklist steps found ('- [ ] 1. ...')"])

    def test_lint_rejects_a_catch_all_tick(self):
        steps = plan.parse("- [x] 1-5 done. Done when: all (~5 min)\n- [x] 6. b. Done when: x (~5 min)\n")
        self.assertIn("catch-all tick", "\n".join(plan.lint(steps)))
        self.assertEqual(plan.lint(plan.parse("- [x] 1. one. Done when: x (~5 min)\n- [x] 2. two. Done when: y (~5 min)\n")), [])

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
            r = subprocess.run([str(_load.HOME / "bin" / "zed-plan"), "lint", str(f)], capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "T1-x-plan.md: ok"))
            r = subprocess.run([str(_load.HOME / "bin" / "zed-plan"), "progress", str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn("1/3 done, ~80 min", r.stdout)
            self.assertEqual(subprocess.run([str(_load.HOME / "bin" / "zed-plan"), "lint", d + "/nope"], capture_output=True).returncode, 2)


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
            r = subprocess.run([str(_load.HOME / "bin" / "zed-plan"), "split", str(f), "--out", str(out)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("wave 1  B: Export button (~90 min)  after A", r.stdout)
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["A-export-endpoint.md", "B-export-button.md", "C-docs.md"])
            text = (out / "B-export-button.md").read_text()
            self.assertIn("Depends on: unit A", text)
            self.assertIn("button downloads the file in a browser", text)
            f.write_text(DESIGN.replace("- Depends on: A", "- Depends on: Z"))
            out2 = Path(d) / "none"
            r = subprocess.run([str(_load.HOME / "bin" / "zed-plan"), "split", str(f), "--out", str(out2)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertFalse(out2.exists())
            self.assertEqual(subprocess.run([str(_load.HOME / "bin" / "zed-plan"), "split", d + "/nope"], capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
