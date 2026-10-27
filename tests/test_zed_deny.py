import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import _load

import zeddeny
import zedemployee as emp

deny = _load.load("zed-deny")


class DecideTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.wt, self.profile, self.evidence = root / "wt", root / "profile", root / "evidence"
        for d in (self.wt / "src", self.profile / "employees" / "quill", self.evidence):
            d.mkdir(parents=True)
        self.manifests = {h: emp.load_manifest(h) for h in emp.hat_names()}

    def call(self, hat, tool, **inp):
        payload = {"tool_name": tool, "tool_input": inp, "cwd": str(self.wt)}
        return zeddeny.decide(payload, self.manifests[hat], self.wt, self.profile)

    def bash(self, hat, command):
        return self.call(hat, "Bash", command=command)

    def test_a_read_only_hat_may_not_edit_the_worktree_but_may_write_evidence_outside_it(self):
        self.assertIn("denied", self.call("review", "Edit", file_path=str(self.wt / "src" / "a.py")))
        self.assertIn("denied", self.call("qa", "Write", file_path="src/a.py"))  # relative to cwd
        self.assertIsNone(self.call("qa", "Write", file_path=str(self.evidence / "report.md")))
        self.assertIsNone(self.bash("qa", f"echo ok > {self.evidence}/log.txt"))
        self.assertIn("denied", self.bash("qa", "echo x > src/a.py"))
        self.assertIn("denied", self.bash("review", f"rm {self.wt}/src/a.py"))
        self.assertIn("denied", self.bash("review", "sed -i s/a/b/ src/a.py"))

    def test_build_edits_the_worktree_but_never_the_employee_records(self):
        self.assertIsNone(self.call("build", "Edit", file_path=str(self.wt / "src" / "a.py")))
        for hat in self.manifests:
            record = str(self.profile / "employees" / "quill" / "memory" / "repos" / "api.md")
            self.assertIn("zed-employee remember", self.call(hat, "Edit", file_path=record), hat)
            self.assertIn("denied", self.bash(hat, f"echo x >> {record}"), hat)
            self.assertIsNone(self.call(hat, "Edit", file_path=str(self.profile / "config" / "projects.json")), hat)

    def test_outward_commands_are_denied_by_prefix_whatever_the_program_path_or_env(self):
        for hat in self.manifests:
            for command in ("gh pr comment 12 --body hi", "gh pr review 12 --approve", "/opt/bin/bb pr comment 3 -m x",
                            "FOO=1 gh pr comment 1", "echo a && gh pr review 1", "(gh pr comment 1)"):
                self.assertIn("denied to this hat", self.bash(hat, command), (hat, command))
            self.assertIsNone(self.bash(hat, "gh pr view 12"), hat)
            self.assertIsNone(self.bash(hat, "echo gh pr comment"), hat)

    def test_a_read_only_hat_may_not_write_git_but_a_builder_may(self):
        self.assertIn("denied", self.bash("review", "git commit -m x"))
        self.assertIn("denied", self.bash("qa", f"git -C {self.wt} add -A"))
        self.assertIsNone(self.bash("review", "git log --oneline"))
        self.assertIsNone(self.bash("build", "git commit -m x"))
        self.assertIsNone(self.bash("qa", f"git -C {self.evidence} commit -m x"))  # a repo outside the worktree

    def test_subagents_follow_the_manifest(self):
        self.assertIn("no subagents", self.call("build", "Agent", prompt="x"))
        self.assertIn("no subagents", self.call("review", "Task", prompt="x"))
        self.manifests["review"] = {**self.manifests["review"], "subagents": "read-only"}
        self.assertIsNone(self.call("review", "Agent", prompt="x"))

    def test_copilot_edits_arrive_as_an_apply_patch_text(self):
        patch_text = "*** Begin Patch\n*** Add File: src/b.py\n+x\n*** End Patch\n"
        payload = {"tool_name": "Edit", "tool_input": patch_text, "cwd": str(self.wt)}
        self.assertIn("denied", zeddeny.decide(payload, self.manifests["review"], self.wt, self.profile))
        self.assertIsNone(zeddeny.decide(payload, self.manifests["build"], self.wt, self.profile))
        record = f"*** Begin Patch\n*** Update File: {self.profile}/employees/quill/employee.md\n@@\n-a\n+b\n*** End Patch\n"
        payload["tool_input"] = record
        self.assertIn("zed-employee", zeddeny.decide(payload, self.manifests["build"], self.wt, self.profile))
        payload["tool_input"] = "not a patch"
        self.assertIn("names no file", zeddeny.decide(payload, self.manifests["qa"], self.wt, self.profile))
        self.assertIsNone(zeddeny.decide(payload, self.manifests["build"], self.wt, self.profile))

    def test_glob_regex(self):
        self.assertTrue(zeddeny.glob_regex("**").match("a/b/c.py"))
        self.assertTrue(zeddeny.glob_regex("employees/**").match("employees/quill/x.md"))
        self.assertFalse(zeddeny.glob_regex("employees/**").match("config/x.md"))
        self.assertFalse(zeddeny.glob_regex("*.py").match("a/b.py"))
        self.assertTrue(zeddeny.glob_regex("*.py").match("b.py"))


class HookScriptTests(unittest.TestCase):
    def run_hook(self, payload, env, *argv):
        with patch.dict(os.environ, env, clear=False), patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
                redirect_stderr(io.StringIO()) as err:
            return deny.main(list(argv)), err.getvalue()

    def test_exit_2_with_the_reason_on_stderr_for_a_denied_call(self):
        env = {"ZED_EMPLOYEE": "quill", "ZED_HAT": "review", "ZED_WORKTREE": "/nonexistent-wt"}
        code, err = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "gh pr comment 1"}}, env)
        self.assertEqual(code, 2)
        self.assertIn("zed-deny: `gh pr comment` is denied", err)

    def test_allowed_calls_and_non_employee_sessions_pass(self):
        env = {"ZED_EMPLOYEE": "quill", "ZED_HAT": "review", "ZED_WORKTREE": "/nonexistent-wt"}
        self.assertEqual(self.run_hook({"tool_name": "Bash", "tool_input": {"command": "git log"}}, env)[0], 0)
        plain = {"tool_name": "Bash", "tool_input": {"command": "gh pr comment 1"}}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZED_EMPLOYEE", None)
            os.environ.pop("ZED_HAT", None)
            self.assertEqual(self.run_hook(plain, {})[0], 0)

    def test_fail_open_on_garbage_and_unknown_hat(self):
        env = {"ZED_EMPLOYEE": "quill", "ZED_HAT": "juggle"}
        self.assertEqual(self.run_hook({"tool_name": "Bash", "tool_input": {"command": "gh pr comment 1"}}, env)[0], 0)
        with patch.dict(os.environ, {"ZED_EMPLOYEE": "quill", "ZED_HAT": "review"}), patch.object(sys, "stdin", io.StringIO("{oops")):
            self.assertEqual(deny.main([]), 0)

    def test_explain_prints_the_decision(self):
        env = {"ZED_EMPLOYEE": "quill", "ZED_HAT": "review", "ZED_WORKTREE": "/nonexistent-wt"}
        out = io.StringIO()
        with patch("sys.stdout", out):
            code, _ = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "bb pr comment 1"}}, env, "--explain")
        self.assertEqual(code, 0)
        self.assertIn("deny: `bb pr comment`", out.getvalue())


if __name__ == "__main__":
    unittest.main()
