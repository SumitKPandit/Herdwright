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


if __name__ == "__main__":
    unittest.main()
