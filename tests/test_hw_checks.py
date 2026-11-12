import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _load

checks = _load.load("hw-checks")


class ChecksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "fake"
        (self.repo / "sub").mkdir(parents=True)
        self.repos = {"fake": {"path": str(self.repo), "checks": {
            "lint": "echo linting && true",
            "types": {"cmd": "echo bad type >&2; exit 3", "timeout": 5},
            "test": {"cmd": "pwd", "cwd": "sub"}}}}

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(checks.hwlib, "projects", return_value=self.repos), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = checks.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_stops_at_first_failure_and_shows_the_tail(self):
        code, out, _ = self.run_main("run", "fake")
        self.assertEqual(code, 1)
        self.assertIn("PASS lint", out)
        self.assertIn("FAIL types", out)
        self.assertIn("bad type", out)
        self.assertIn("skipped after the first failure: test", out)
        self.assertIn("CHECKS FAILED", out)

    def test_keep_going_runs_all_and_cwd_is_honoured(self):
        code, out, _ = self.run_main("run", "fake", "--keep-going")
        self.assertEqual(code, 1)
        self.assertIn(f"PASS test", out)
        report = json.loads(self.run_main("run", "fake", "--keep-going", "--json")[1])
        self.assertEqual([r["ok"] for r in report["results"]], [True, False, True])
        self.assertEqual(report["results"][1]["exit"], 3)
        self.assertTrue(report["results"][2]["tail"][0].endswith("/sub"))

    def test_only_selects_checks_and_all_pass_exits_zero(self):
        code, out, _ = self.run_main("run", "fake", "--only", "lint,test")
        self.assertEqual(code, 0)
        self.assertIn("all checks passed", out)
        code, _, err = self.run_main("run", "fake", "--only", "nope")
        self.assertEqual(code, 2)
        self.assertIn("no such check: nope", err)

    def test_dir_overrides_the_repo_folder(self):
        wt = Path(self.tmp.name) / "wt"
        wt.mkdir()
        self.repos["fake"]["checks"] = {"where": "pwd"}
        code, out, _ = self.run_main("run", "fake", "--dir", str(wt), "--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["results"][0]["tail"][0].endswith("/wt"))

    def test_timeout_is_reported_as_a_failure(self):
        self.repos["fake"]["checks"] = {"slow": {"cmd": "sleep 5", "timeout": 1}}
        code, out, _ = self.run_main("run", "fake")
        self.assertEqual(code, 1)
        self.assertIn("timed out after 1s", out)

    def test_config_errors_exit_two(self):
        self.assertEqual(self.run_main("run", "nope")[0], 2)
        self.repos["fake"].pop("checks")
        code, _, err = self.run_main("run", "fake")
        self.assertEqual(code, 2)
        self.assertIn("no `checks` block", err)
        self.repos["fake"]["checks"] = {"x": {"cwd": "sub"}}
        self.assertIn("no command", self.run_main("show", "fake")[2])

    def test_missing_cwd_fails_clearly_and_show_lists_checks(self):
        self.repos["fake"]["checks"] = {"x": {"cmd": "true", "cwd": "gone"}}
        code, out, _ = self.run_main("run", "fake")
        self.assertEqual(code, 1)
        self.assertIn("working directory not found", out)
        self.assertIn("x: true  (in gone)", self.run_main("show", "fake")[1])


if __name__ == "__main__":
    unittest.main()
