import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import _load
import zedlib

guard = _load.load("zed-guard")
GUARD = str(_load.HOME / "bin" / "zed-guard")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *a, check=True):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, env=ENV, check=check)


class PureTests(unittest.TestCase):
    def test_protected(self):
        pats = guard.DEFAULT_PROTECTED
        for b in ("main", "master", "releases/v6.2.1"):
            self.assertTrue(guard.is_protected(b, pats), b)
        for b in ("story/abc-1-x", "chore/releases", "mainline", "deploy", "medusa"):
            self.assertFalse(guard.is_protected(b, pats), b)

    def test_commit_message(self):
        f = guard.commit_message_problem
        self.assertIsNone(f("MENT-123 | Add thing"))
        self.assertIsNone(f("Merge branch 'x'"))
        self.assertIsNotNone(f("add thing"))
        self.assertIsNotNone(f("MENT-123: Add thing"))
        self.assertIsNone(f("MENT-5 | x", jira="MENT-5"))
        self.assertIn("MENT-5 | ", f("MENT-6 | x", jira="MENT-5"))
        self.assertIsNone(f("anything", enforce=False))

    def test_push_problems_ask_the_gate_about_branch_pushes_only(self):
        sha, zero = "b" * 40, guard.ZERO
        asked = []

        def gate(commit):
            asked.append(commit)
            return "no review verdict"
        lines = [f"refs/heads/x {sha} refs/heads/feat {zero}", f"(delete) {zero} refs/heads/gone {sha}",
                 f"refs/tags/v1 {sha} refs/tags/v1 {zero}"]
        problems = guard.push_problems(lines, guard.DEFAULT_PROTECTED, lambda o, n: True, gate)
        self.assertEqual(asked, [sha])
        self.assertEqual(problems[0], "push of 'feat' refused: no review verdict")
        self.assertEqual(guard.push_problems(lines[:1], guard.DEFAULT_PROTECTED, lambda o, n: True, lambda c: None), [])
        self.assertEqual(guard.push_problems(lines[:1], guard.DEFAULT_PROTECTED, lambda o, n: True), [])  # no gate: as before

    def test_push_problems(self):
        anc = lambda old, new: old == "a" * 40
        sha, zero = "b" * 40, guard.ZERO
        lines = [f"refs/heads/x {sha} refs/heads/main {'a' * 40}",
                 f"refs/heads/x {sha} refs/heads/feat {'c' * 40}",
                 f"(delete) {zero} refs/heads/feat2 {sha}",
                 f"refs/heads/x {sha} refs/heads/feat3 {'a' * 40}",
                 f"refs/tags/v1 {sha} refs/tags/v1 {zero}"]
        text = "\n".join(guard.push_problems(lines, guard.DEFAULT_PROTECTED, anc))
        self.assertIn("protected branch 'main'", text)
        self.assertIn("force-push to 'feat'", text)
        self.assertIn("deleting remote branch 'feat2'", text)
        self.assertNotIn("feat3", text)
        self.assertNotIn("v1", text)

    def test_stale_lock(self):
        self.assertTrue(guard.stale_lock(600, False))
        self.assertFalse(guard.stale_lock(600, True))
        self.assertFalse(guard.stale_lock(10, False))


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        r = Path(self.tmp.name)
        git(r, "init", "-q", "--bare", "-b", "main", "origin.git")
        git(r, "clone", "-q", "origin.git", "clone")
        git(r / "clone", "commit", "-q", "--allow-empty", "-m", "init")
        git(r / "clone", "push", "-q", "origin", "main")
        # the repo already has its own hooks (like husky): a pre-commit that records it ran
        (r / "repohooks").mkdir()
        hook = r / "repohooks" / "pre-commit"
        hook.write_text(f"#!/bin/sh\necho ran >> {r}/chain.log\n")
        hook.chmod(0o755)
        git(r / "clone", "config", "core.hooksPath", str(r / "repohooks"))
        self.wt = r / "wt"
        git(r / "clone", "worktree", "add", "-q", "-b", "task/ment-9-x", str(self.wt), "origin/main")
        self.r = r

    def install(self, *extra, profile=None):
        env = {**os.environ, "ZED_PROFILE_DIR": profile or str(self.r / "no-profile")}
        out = subprocess.run([GUARD, "install", str(self.wt), *extra], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)

    def commit(self, msg, name="f.txt"):
        (self.wt / name).write_text(msg)
        git(self.wt, "add", name)
        return git(self.wt, "commit", "-q", "-m", msg, check=False)

    def test_commit_rules_and_chaining(self):
        self.install("--jira", "MENT-9")
        bad = self.commit("fix stuff")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("must start with 'MENT-9 | '", bad.stderr)
        ok = self.commit("MENT-9 | Add file")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("ran", (self.r / "chain.log").read_text())  # the repo's own pre-commit still ran
        # the main checkout is untouched by the per-worktree install
        self.assertEqual(git(self.r / "clone", "config", "--get", "core.hooksPath").stdout.strip(), str(self.r / "repohooks"))

    def test_no_commit_format_and_protected_branch(self):
        self.install("--no-commit-format")
        self.assertEqual(self.commit("anything goes").returncode, 0)
        git(self.wt, "checkout", "-q", "-b", "main2")
        self.assertEqual(self.commit("on a non-default name", "g.txt").returncode, 0)  # core knows only generic names
        git(self.wt, "checkout", "-q", "-b", "master")
        r = self.commit("on master", "h.txt")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("protected branch 'master'", r.stderr)

    def test_profile_settings_drive_branches_and_format(self):
        prof = self.r / "profile"
        (prof / "config").mkdir(parents=True)
        (prof / "config" / "settings.json").write_text(json.dumps({"guard": {
            "protected": ["deploy", "rel-*"], "commit_format": r"^TKT#\d+: \S", "commit_example": "TKT#1: Add it"}}))
        self.install("--base", "dev", profile=str(prof))
        bad = self.commit("ABC-1 | wrong format")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("TKT#1: Add it", bad.stderr)
        self.assertEqual(self.commit("TKT#7: right format", "g.txt").returncode, 0)
        for branch in ("deploy", "rel-2", "dev"):
            git(self.wt, "checkout", "-q", "-B", branch)
            r = self.commit(f"TKT#8: on {branch}", f"{branch}.txt")
            self.assertIn(f"protected branch '{branch}'", r.stderr)

    def test_an_invalid_commit_format_is_refused_at_install(self):
        prof = self.r / "profile"
        (prof / "config").mkdir(parents=True)
        for bad in ("(", ["x"]):
            (prof / "config" / "settings.json").write_text(json.dumps({"guard": {"commit_format": bad}}))
            env = {**os.environ, "ZED_PROFILE_DIR": str(prof)}
            out = subprocess.run([GUARD, "install", str(self.wt)], capture_output=True, text=True, env=env)
            self.assertEqual(out.returncode, 2, bad)
            self.assertNotIn("Traceback", out.stderr)
        (prof / "config" / "settings.json").write_text(json.dumps({"guard": {"commit_format": "("}}))
        env = {**os.environ, "ZED_PROFILE_DIR": str(prof)}
        out = subprocess.run([GUARD, "install", str(self.wt)], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 2)
        self.assertIn("not a valid regex", out.stderr)

    def test_empty_commit_message_file_is_a_readable_refusal(self):
        self.install("--jira", "ABC-9")
        empty = self.r / "msg"
        empty.write_text("")
        r = subprocess.run([GUARD, "hook", "commit-msg", str(empty)], cwd=self.wt, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("must start with 'ABC-9 | '", r.stderr)

    def test_push_rules(self):
        self.install("--jira", "MENT-9")
        self.assertEqual(self.commit("MENT-9 | one").returncode, 0)
        ok = git(self.wt, "push", "-q", "origin", "HEAD:refs/heads/task/ment-9-x", check=False)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        to_main = git(self.wt, "push", "origin", "HEAD:refs/heads/main", check=False)
        self.assertNotEqual(to_main.returncode, 0)
        self.assertIn("protected branch 'main'", to_main.stderr)
        git(self.wt, "reset", "-q", "--hard", "HEAD~1")
        self.assertEqual(self.commit("MENT-9 | two", "h.txt").returncode, 0)
        forced = git(self.wt, "push", "-f", "origin", "HEAD:refs/heads/task/ment-9-x", check=False)
        self.assertNotEqual(forced.returncode, 0)
        self.assertIn("force-push", forced.stderr)
        gone = git(self.wt, "push", "origin", ":task/ment-9-x", check=False)
        self.assertIn("deleting remote branch", gone.stderr)

    def test_installing_again_with_the_gate_turns_it_on_in_a_guarded_worktree(self):
        self.install("--jira", "MENT-9")
        cfg = Path(git(self.wt, "config", "--get", "core.hooksPath").stdout.strip()) / "config.json"
        self.assertFalse(json.loads(cfg.read_text())["gate"])
        again = subprocess.run([GUARD, "install", str(self.wt), "--gate"], capture_output=True, text=True,
                               env={**os.environ, "ZED_PROFILE_DIR": str(self.r / "no-profile")})
        self.assertEqual(again.returncode, 2)
        self.assertIn("already pointing", again.stderr)
        self.assertTrue(json.loads(cfg.read_text())["gate"])

    def reinstall(self, *flags):
        return subprocess.run([GUARD, "install", str(self.wt), *flags], capture_output=True, text=True,
                              env={**os.environ, "ZED_PROFILE_DIR": str(self.r / "no-profile")})

    def test_a_refused_reinstall_without_the_gate_never_turns_an_enabled_gate_off(self):
        self.install("--jira", "MENT-9", "--gate")
        cfg = Path(git(self.wt, "config", "--get", "core.hooksPath").stdout.strip()) / "config.json"
        before = cfg.read_text()
        again = self.reinstall("--jira", "MENT-9")
        self.assertEqual(again.returncode, 2)
        self.assertIn("already pointing", again.stderr)
        self.assertEqual(cfg.read_text(), before)
        self.assertTrue(json.loads(cfg.read_text())["gate"])

    def test_only_remove_then_install_switches_the_gate_off(self):
        self.install("--jira", "MENT-9", "--gate")
        cfg = Path(git(self.wt, "config", "--get", "core.hooksPath").stdout.strip()) / "config.json"
        self.assertEqual(subprocess.run([GUARD, "remove", str(self.wt)], capture_output=True, text=True).returncode, 0)
        self.install("--jira", "MENT-9")
        self.assertFalse(json.loads(cfg.read_text())["gate"])

    def test_a_gated_worktree_refuses_a_push_without_review_and_qa(self):
        profile = self.r / "gate-profile"
        (profile / "db").mkdir(parents=True)
        (profile / "profile.md").write_text("# test profile\n")
        self.install("--jira", "MENT-9", "--gate", profile=str(profile))
        self.assertEqual(self.commit("MENT-9 | one").returncode, 0)
        env = {**os.environ, "ZED_PROFILE_DIR": str(profile), "ZED_GATE_OVERRIDE": "worker says so"}  # only the user may override
        refused = subprocess.run(["git", "push", "origin", "HEAD:refs/heads/task/ment-9-x"], cwd=self.wt, capture_output=True, text=True, env=env)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("push of 'task/ment-9-x' refused", refused.stderr)
        self.assertIn("no review verdict recorded", refused.stderr)

    def test_an_ungated_worktree_pushes_without_asking_the_gate(self):
        self.install("--jira", "MENT-9")
        self.assertEqual(self.commit("MENT-9 | one").returncode, 0)
        ok = git(self.wt, "push", "-q", "origin", "HEAD:refs/heads/task/ment-9-x", check=False)
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_remove_restores_repo_hooks(self):
        self.install("--jira", "MENT-9")
        subprocess.run([GUARD, "remove", str(self.wt)], check=True, capture_output=True)
        self.assertEqual(self.commit("any message", "z.txt").returncode, 0)
        self.assertEqual(git(self.wt, "config", "--get", "core.hooksPath").stdout.strip(), str(self.r / "repohooks"))

    def test_audit_worktree_findings(self):
        s = {"id": 1, "agent": "a", "repo": "none", "base": "main", "branch": "task/ment-9-x", "worktree": str(self.wt)}
        self.assertIn("hooks not installed", "\n".join(guard.audit_worktree(s, False, 0)))
        self.install("--jira", "MENT-9")
        self.assertEqual(guard.audit_worktree(s, False, 0), [])
        git(self.wt, "checkout", "-q", "-b", "other")
        self.assertIn("expected 'task/ment-9-x'", "\n".join(guard.audit_worktree(s, False, 0)))
        lock = Path(git(self.wt, "rev-parse", "--absolute-git-dir").stdout.strip()) / "index.lock"
        lock.write_text("")
        old = lock.stat().st_mtime - 3600
        os.utime(lock, (old, old))
        self.assertIn("stale lock", "\n".join(guard.audit_worktree(s, False, old + 3600)))
        self.assertNotIn("stale lock", "\n".join(guard.audit_worktree(s, True, old + 3600)))


if __name__ == "__main__":
    unittest.main()
