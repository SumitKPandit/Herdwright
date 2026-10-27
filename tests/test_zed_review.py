import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _load

review = _load.load("zed-review")

KEY = "AKIA" + "ABCDEFGHIJKLMNOP"   # built in two parts so this file holds no key-shaped literal
BIG = "".join(f"+    step_{i} = compute({i})\n" for i in range(55))
DIFF = f"""diff --git a/app/service.py b/app/service.py
index 111..222 100644
--- a/app/service.py
+++ b/app/service.py
@@ -10,3 +10,8 @@ def build_report(rows):
     keep = 1
+    print("debugging", rows)
+    # old = fetch(rows)
+    # TODO handle empty rows
+    key = "{KEY}"
     tail = 2
diff --git a/app/big.py b/app/big.py
--- a/app/big.py
+++ b/app/big.py
@@ -1,2 +1,60 @@ def giant(x):
 a = 1
{BIG}diff --git a/app/models.py b/app/models.py
--- a/app/models.py
+++ b/app/models.py
@@ -1,1 +1,2 @@
 x = 1
+y = 2
diff --git a/tests/test_models.py b/tests/test_models.py
--- a/tests/test_models.py
+++ b/tests/test_models.py
@@ -1,1 +1,2 @@
 import x
+print("tests may print")
diff --git a/app/migrations/0007_add_col.py b/app/migrations/0007_add_col.py
new file mode 100644
--- /dev/null
+++ b/app/migrations/0007_add_col.py
@@ -0,0 +1,2 @@
+# generated
+ops = []
diff --git a/package.json b/package.json
--- a/package.json
+++ b/package.json
@@ -5,3 +5,4 @@
   "dependencies": {{
-    "left-pad": "^1.0.0",
+    "left-pad": "^1.1.0",
+    "lodash": "^4.17.0"
   }}
diff --git a/requirements.txt b/requirements.txt
--- a/requirements.txt
+++ b/requirements.txt
@@ -1,1 +1,2 @@
 flask==3.0
+requests>=2.31
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,1 +1,2 @@
 hi
+print("docs are exempt")
"""


class PrepTests(unittest.TestCase):
    def setUp(self):
        self.facts = review.analyze(DIFF)

    def test_stats_and_files_without_a_test_change(self):
        self.assertEqual(self.facts["files"], 8)
        self.assertEqual(self.facts["tests_changed"], ["tests/test_models.py"])
        # models.py has test_models.py; service.py and big.py have none; migrations, docs and package files are not source
        self.assertEqual(self.facts["source_without_test"], ["app/service.py", "app/big.py"])
        self.assertEqual(review.files_without_test(review.parse_diff(DIFF.split("diff --git a/tests")[0])),
                         ["app/service.py", "app/big.py", "app/models.py"])

    def test_debug_leftovers_use_new_file_line_numbers_and_skip_tests_and_docs(self):
        found = {(d["file"], d["line"], d["kind"]) for d in self.facts["debug_leftovers"]}
        self.assertIn(("app/service.py", 11, "print("), found)
        self.assertIn(("app/service.py", 12, "commented-out code"), found)
        self.assertIn(("app/service.py", 13, "TODO/FIXME"), found)
        self.assertFalse([d for d in self.facts["debug_leftovers"] if d["file"] in ("tests/test_models.py", "README.md")])

    def test_new_dependencies_ignore_version_bumps(self):
        got = {(d["file"], d["name"]) for d in self.facts["new_dependencies"]}
        self.assertEqual(got, {("package.json", "lodash"), ("requirements.txt", "requests")})

    def test_migrations_large_functions_and_secrets_without_values(self):
        self.assertEqual(self.facts["migrations"], ["app/migrations/0007_add_col.py"])
        self.assertEqual(self.facts["large_changes"], [{"file": "app/big.py", "function": "def giant(x):", "changed": 55}])
        self.assertEqual([(s["file"], s["line"], s["kind"]) for s in self.facts["secrets"]], [("app/service.py", 14, "AWS access key")])
        self.assertNotIn(KEY, json.dumps(self.facts))
        self.assertNotIn(KEY, "\n".join(review.render_facts(self.facts)))

    def test_clean_diff_says_none(self):
        text = "\n".join(review.render_facts(review.analyze("")))
        for fact in ("Debug leftovers in added lines: none", "New dependencies: none", "Migrations: none", "Secret-looking added lines (values not shown): none"):
            self.assertIn(fact, text)

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli_diff_file_dir_and_pr_with_ci(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.diff"
            f.write_text(DIFF)
            code, out, _ = self.run_main("prep", "--diff", str(f))
            self.assertEqual(code, 0)
            self.assertIn("Migrations: 1", out)
            self.assertEqual(self.run_main("prep", "--diff", d + "/nope")[0], 2)
            self.assertEqual(self.run_main("prep")[0], 2)
            self.assertEqual(self.run_main("prep", "--dir", d)[0], 2)
            env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"}
            repo = Path(d) / "repo"
            for cmd in (["init", "-q", "-b", "main", str(repo)], ["-C", str(repo), "commit", "-q", "--allow-empty", "-m", "a"],
                        ["-C", str(repo), "checkout", "-qb", "feat"]):
                subprocess.run(["git", *cmd], check=True, env=env)
            (repo / "app.py").write_text("print('x')\n")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "b"], check=True, env=env)
            code, out, _ = self.run_main("prep", "--dir", str(repo), "--base", "main", "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["debug_leftovers"][0]["file"], "app.py")
        with patch.object(review, "fetch_diff", return_value=DIFF), patch.object(review, "ci_line", return_value="green: https://github.com/o/r/pull/1"):
            code, out, _ = self.run_main("prep", "https://github.com/o/r/pull/1")
        self.assertIn("CI: green: https://github.com/o/r/pull/1", out)
        with patch.object(review, "fetch_diff", side_effect=RuntimeError("gh pr diff failed: nope")):
            code, _, err = self.run_main("prep", "https://github.com/o/r/pull/1")
        self.assertEqual(code, 2)
        self.assertIn("gh pr diff failed", err)


GOOD_DRAFT = """# Review of PR 12

## Findings
- [blocker] app/guard.py:42 the role check is skipped on the error path.
  Fix: call require_role before the early return.
- [should-fix] app/views.py:10 name `d` hides intent. Suggest: rename to `deadline`.
- [nit] app/views.py:11 stray blank line. Fix: remove it.

Verdict: blockers
"""


class DraftTests(unittest.TestCase):
    def problems(self, text):
        return review.check_draft(text)["problems"]

    def test_good_draft_passes_and_counts(self):
        r = review.check_draft(GOOD_DRAFT)
        self.assertEqual((r["problems"], r["findings"], r["verdict"]), ([], 3, "blockers"))
        self.assertEqual(self.problems("Looks fine.\n\nVerdict: clean\n"), [])

    def test_each_missing_part_is_refused(self):
        self.assertIn("no file:line", "\n".join(self.problems(GOOD_DRAFT.replace("app/guard.py:42", "the guard"))))
        self.assertIn("severity must be one of", "\n".join(self.problems(GOOD_DRAFT.replace("[nit]", "[minor]"))))
        self.assertIn("severity must be one of", "\n".join(self.problems(GOOD_DRAFT.replace("[nit]", "[]"))))
        self.assertIn("no suggested change", "\n".join(self.problems(GOOD_DRAFT.replace("Suggest: rename to `deadline`.", "rename it."))))
        self.assertIn("no verdict", "\n".join(self.problems(GOOD_DRAFT.replace("Verdict: blockers", ""))))
        self.assertIn("2 verdict lines", "\n".join(self.problems(GOOD_DRAFT + "Verdict: clean\n")))
        self.assertIn("must be 'clean' or 'blockers'", "\n".join(self.problems(GOOD_DRAFT.replace("blockers\n", "approve\n").replace("Verdict: blockers", "Verdict: approve"))))

    def test_verdict_must_agree_with_the_findings(self):
        self.assertIn("verdict is clean but a finding is a blocker", self.problems(GOOD_DRAFT.replace("Verdict: blockers", "Verdict: clean")))
        self.assertIn("verdict is blockers but no finding is a blocker", self.problems(GOOD_DRAFT.replace("[blocker]", "[should-fix]")))

    def test_anything_addressed_to_a_pr_is_refused(self):
        for line in ("Run: gh pr review 12 --approve", "bb pr comment 3 'hi'", "curl https://api.bitbucket.org/2.0/repositories/a/b/pullrequests/9/comments", "/approve"):
            self.assertTrue([p for p in self.problems(GOOD_DRAFT + line + "\n") if "never posted" in p], line)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "draft.md"
            f.write_text(GOOD_DRAFT)
            run = lambda *a: subprocess.run([str(_load.HOME / "bin" / "zed-review"), *a], capture_output=True, text=True)
            r = run("check-draft", str(f))
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "draft.md: ok (3 finding(s), verdict blockers)"))
            f.write_text(GOOD_DRAFT.replace("Verdict: blockers", ""))
            r = run("check-draft", str(f))
            self.assertEqual(r.returncode, 1)
            self.assertIn("no verdict", r.stdout)
            self.assertEqual(run("check-draft", d + "/nope").returncode, 2)


if __name__ == "__main__":
    unittest.main()
