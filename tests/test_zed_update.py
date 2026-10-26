import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import _load
import zedlib

up = _load.load("zed-update")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, env=ENV, check=True).stdout


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        r = self.r = Path(self.tmp.name).resolve()
        git(r, "init", "-q", "--bare", "-b", "main", "origin.git")
        git(r, "clone", "-q", "origin.git", "zed")
        (r / "zed" / "core.txt").write_text("1\n")
        git(r / "zed", "add", "."); git(r / "zed", "commit", "-q", "-m", "core 1"); git(r / "zed", "push", "-q", "origin", "main")
        git(r / "zed", "checkout", "-q", "-b", "profile/me")
        (r / "zed" / "profile.txt").write_text("p\n")
        git(r / "zed", "add", "."); git(r / "zed", "commit", "-q", "-m", "profile")
        git(r, "clone", "-q", "origin.git", "other")
        self.old = up.HOME
        up.HOME = r / "zed"
        self.addCleanup(lambda: setattr(up, "HOME", self.old))

    def push_core(self, name="core.txt", text="2\n", msg="core 2"):
        (self.r / "other" / name).write_text(text)
        git(self.r / "other", "add", "."); git(self.r / "other", "commit", "-q", "-m", msg); git(self.r / "other", "push", "-q", "origin", "main")

    def run_cli(self, *args):
        return subprocess.run([str(_load.HOME / "bin" / "zed-update"), *args], capture_output=True, text=True)

    def a(self, **kw):
        import argparse
        return argparse.Namespace(**{"remote": "origin", "no_fetch": False, "json": False, **kw})

    def test_check_and_apply(self):
        self.assertEqual(up.state("origin", True)["pending"], [])
        self.push_core()
        st = up.state("origin", True)
        self.assertEqual((len(st["pending"]), st["would_conflict"], st["branch"]), (1, False, "profile/me"))
        self.assertIsNone(up.refusal(st))
        self.assertEqual(up.cmd_apply(self.a()), 0)
        self.assertEqual((self.r / "zed" / "core.txt").read_text(), "2\n")
        self.assertTrue((self.r / "zed" / "profile.txt").exists())
        self.assertEqual(up.state("origin", False)["pending"], [])
        self.assertEqual(git(self.r / "zed", "rev-parse", "main"), git(self.r / "zed", "rev-parse", "origin/main"))

    def test_refuses_dirty_main_and_conflict(self):
        self.push_core()
        (self.r / "zed" / "scratch").write_text("x")
        git(self.r / "zed", "add", "scratch")
        self.assertIn("uncommitted", up.refusal(up.state("origin", True)))
        git(self.r / "zed", "reset", "-q", "scratch"); (self.r / "zed" / "scratch").unlink()
        # conflicting change on the profile branch
        (self.r / "zed" / "core.txt").write_text("mine\n")
        git(self.r / "zed", "commit", "-qam", "edit core on profile")
        self.assertIn("conflicts", up.refusal(up.state("origin", False)))
        before = git(self.r / "zed", "rev-parse", "HEAD")
        self.assertEqual(up.cmd_apply(self.a()), 2)
        self.assertEqual(git(self.r / "zed", "rev-parse", "HEAD"), before)
        git(self.r / "zed", "checkout", "-q", "main")
        self.assertIn("on main", up.refusal(up.state("origin", False)))

    def test_check_exit_codes(self):
        self.assertEqual(up.cmd_check(self.a()), 0)
        self.push_core()
        self.assertEqual(up.cmd_check(self.a()), 1)


if __name__ == "__main__":
    unittest.main()
