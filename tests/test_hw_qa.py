import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _load

qa = _load.load("hw-qa")

BRIEF = """# Brief — T9 · ABC-9 · Export

## Goal
Export the table.

## Known open points (do not assume — ask if they matter)
- Large tables over 10k rows
- none

## QA
- How: browser.

## Definition of done
- A CSV downloads with the current filters
- Button is hidden for read-only users
  and shown to editors
- QA done, self-review done, committed and PR raised per the user's rules against main.
- Finish with an HW:DONE block.
"""

GOOD = """# QA report: T9

URL: http://localhost:3000
Branch: feature/export
Commit: abc1234
Console errors: none

## Cases
### 1. CSV downloads
Result: pass
Evidence: shots/one.png, trace.zip
### 2. Read-only user
Result: fail
Evidence: shots/two.png
Notes: button visible

## Verdict

Verdict: blockers
"""


class PlanTests(unittest.TestCase):
    def test_sections_and_cases(self):
        self.assertEqual(qa.bullets(qa.section(BRIEF, "definition of done"))[1], "Button is hidden for read-only users and shown to editors")
        cases = qa.build_cases(BRIEF)
        happy = [t for g, t in cases if g == "Happy path"]
        self.assertEqual(happy, ["A CSV downloads with the current filters", "Button is hidden for read-only users and shown to editors"])
        edge = [t for g, t in cases if g == "Edge cases"]
        self.assertEqual(edge[0], "Large tables over 10k rows")
        self.assertEqual(len(edge), 1 + len(qa.EDGE_PROMPTS))   # 'none' is dropped
        self.assertEqual([g for g, _ in cases][-1], "Regression")

    def test_acceptance_criteria_section_and_regression_from_changed_files(self):
        text = BRIEF + "\n## Acceptance criteria\n1. Totals match the table\n"
        happy = [t for g, t in qa.build_cases(text, ["app/export.py"]) if g == "Happy path"]
        self.assertEqual(happy[0], "Totals match the table")
        self.assertIn(("Regression", "Nearby behaviour of app/export.py still works"), qa.build_cases(BRIEF, ["app/export.py"]))
        self.assertTrue(qa.build_cases("# nothing")[0][1].startswith("The main flow"))

    def test_skeleton_is_refused_by_report_check_until_filled(self):
        with tempfile.TemporaryDirectory() as d:
            brief = Path(d) / "T9-app.md"
            brief.write_text(BRIEF)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(qa.main(["plan", str(brief), "--out", str(Path(d) / "qa")]), 0)
            report = (Path(d) / "qa" / "report.md").read_text()
            self.assertIn("### 1. A CSV downloads with the current filters", report)
            result = qa.check_report(report, Path(d))
            self.assertGreaterEqual(len(result["problems"]), 10)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(qa.main(["plan", str(brief), "--out", str(Path(d) / "qa")]), 2)   # never over an existing report
                self.assertEqual(qa.main(["plan", "T99999"]), 2)

    def test_plan_regression_cases_from_a_real_branch(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"}
            repo = Path(d) / "repo"
            for cmd in (["init", "-q", "-b", "main", str(repo)], ["-C", str(repo), "commit", "-q", "--allow-empty", "-m", "a"], ["-C", str(repo), "checkout", "-qb", "feat"]):
                subprocess.run(["git", *cmd], check=True, env=env)
            for name in ("app.py", "README.md", "tests/test_app.py"):
                (repo / name).parent.mkdir(exist_ok=True)
                (repo / name).write_text("x\n")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "b"], check=True, env=env)
            self.assertEqual(qa.changed_sources(repo, "main"), ["app.py"])
            brief = Path(d) / "b.md"
            brief.write_text(BRIEF)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                qa.main(["plan", str(brief), "--dir", str(repo), "--base", "main", "--json"])
            self.assertIn("Nearby behaviour of app.py still works", [c["title"] for c in json.loads(out.getvalue())["cases"]])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(qa.main(["plan", str(brief), "--dir", str(repo), "--base", "nobranch"]), 2)

    def test_task_id_finds_the_brief_but_not_the_plan(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "T9-app.md").write_text(BRIEF)
            (Path(d) / "T9-app-plan.md").write_text("- [ ] 1. x")
            with patch.object(qa.hwlib, "BRIEFS_DIR", Path(d)):
                self.assertEqual(qa.find_brief("T9").name, "T9-app.md")
                self.assertIsNone(qa.find_brief("T8"))


class ReportCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "shots").mkdir()
        for f in ("shots/one.png", "shots/two.png", "trace.zip"):
            (self.dir / f).write_text("x")

    def problems(self, text):
        return qa.check_report(text, self.dir)["problems"]

    def test_complete_report_passes(self):
        r = qa.check_report(GOOD, self.dir)
        self.assertEqual((r["problems"], r["cases"], r["verdict"]), ([], 2, "blockers"))

    def test_each_listed_failure_is_refused(self):
        self.assertIn("case 1 (CSV downloads): no result", "\n".join(self.problems(GOOD.replace("Result: pass", "Result: ___"))))
        self.assertIn("not one of", "\n".join(self.problems(GOOD.replace("Result: pass", "Result: ok"))))
        self.assertIn("evidence file 'shots/gone.png' not found", "\n".join(self.problems(GOOD.replace("shots/two.png", "shots/gone.png"))))
        for key in ("URL", "Branch", "Commit"):
            text = "\n".join(f"{key}: ___" if line.startswith(f"{key}:") else line for line in GOOD.splitlines())
            self.assertIn(f"environment not recorded: {key}", self.problems(text))
        self.assertIn("console errors not recorded", "\n".join(self.problems(GOOD.replace("Console errors: none\n", ""))))
        self.assertIn("no verdict", "\n".join(self.problems(GOOD.replace("Verdict: blockers", ""))))
        self.assertIn("no cases", "\n".join(self.problems("URL: u\nBranch: b\nCommit: c\nConsole errors: none\nVerdict: clean\n")))

    def test_verdict_must_agree_with_the_cases(self):
        self.assertIn("verdict is clean but a case failed or is blocked", self.problems(GOOD.replace("Verdict: blockers", "Verdict: clean")))
        self.assertIn("verdict is blockers but no case failed or is blocked", self.problems(GOOD.replace("Result: fail", "Result: pass")))
        self.assertIn("2 verdict lines", "\n".join(self.problems(GOOD + "Verdict: blockers\n")))
        self.assertEqual(self.problems(GOOD.replace("Result: fail", "Result: skipped").replace("Verdict: blockers", "Verdict: clean")), [])

    def test_evidence_none_and_absolute_paths(self):
        absolute = str(self.dir / "trace.zip")
        text = GOOD.replace("Evidence: shots/two.png", f"Evidence: none").replace("Evidence: shots/one.png, trace.zip", f"Evidence: `{absolute}`")
        self.assertEqual(self.problems(text), [])

    def test_cli(self):
        (self.dir / "report.md").write_text(GOOD)
        run = lambda *a: subprocess.run([str(_load.HOME / "bin" / "hw-qa"), *a], capture_output=True, text=True)
        r = run("report-check", str(self.dir))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("(2 case(s), verdict blockers)", r.stdout)
        (self.dir / "report.md").write_text(GOOD.replace("Commit: abc1234", "Commit: ___"))
        r = run("report-check", str(self.dir / "report.md"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("environment not recorded: Commit", r.stdout)
        self.assertEqual(run("report-check", str(self.dir / "nowhere")).returncode, 2)


if __name__ == "__main__":
    unittest.main()
