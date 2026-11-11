import io
import json
import os
import shutil
import sys
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import _load

import zeddeny
import zedemployee as emp

HOME = Path(__file__).resolve().parents[1]
deny = _load.load("zed-deny")


class DecideTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.wt, self.profile, self.evidence = root / "wt", root / "profile", root / "evidence"
        for d in (self.wt / "src", self.profile / "briefs" / "sub", self.evidence):
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

    def test_only_build_edits_the_worktree_and_the_profile_config(self):
        self.assertIsNone(self.call("build", "Edit", file_path=str(self.wt / "src" / "a.py")))
        config = str(self.profile / "config" / "projects.json")
        self.assertIsNone(self.call("build", "Edit", file_path=config))
        for hat in ("review", "qa"):
            self.assertIn("denied", self.call(hat, "Edit", file_path=config), hat)
            self.assertIn("denied", self.bash(hat, f"echo x >> {config}"), hat)

    def test_outward_commands_are_denied_by_prefix_whatever_the_program_path_or_env(self):
        for hat in self.manifests:
            for command in ("gh pr comment 12 --body hi", "gh pr review 12 --approve", "/opt/bin/gh pr comment 3 -b x",
                            "FOO=1 gh pr comment 1", "echo a && gh pr review 1", "(gh pr comment 1)"):
                self.assertIn("denied to this hat", self.bash(hat, command), (hat, command))
            self.assertIsNone(self.bash(hat, "gh pr view 12"), hat)
            self.assertIsNone(self.bash(hat, "echo gh pr comment"), hat)

    def test_cd_in_the_command_line_moves_where_relative_targets_resolve(self):
        emp_dir = self.profile / "briefs"
        for command in (f"cd {emp_dir}/sub && touch x", f"cd {emp_dir} && touch sub/x", f"cd {emp_dir}; echo y > sub/x"):
            self.assertIn("denied", self.bash("review", command), command)
        self.assertIsNone(self.bash("review", f"cd {self.evidence} && touch x"))  # was a false deny
        self.assertIn("denied", self.bash("review", f"cd {self.evidence} && cd {self.wt}/src && touch x"))
        self.assertIsNone(self.bash("build", "cd src && touch x"))

    def test_a_cd_the_shell_expands_leaves_relative_targets_unchecked(self):
        emp_dir = self.profile / "briefs"
        for command in (f"cd $D && touch x", f"D={emp_dir}/sub; cd $D && touch x", f'cd "$E" && touch x',
                        f"cd -- {emp_dir} && touch sub/x", f"pushd {emp_dir} && touch sub/x", "cd - && touch x"):
            self.assertIn("denied" if "--" in command or "pushd" in command else "cannot be checked", self.bash("review", command), command)
        self.assertIsNone(self.bash("review", f"cd $D && touch {self.evidence}/x"))  # absolute targets stay checkable

    def test_git_writes_after_an_unknown_cd_are_denied_for_a_read_only_hat(self):
        for hat in ("review", "qa"):
            for command in ("cd $D && git commit -m x", "cd $D && git push", "cd - && git commit -m x",
                            'cd "$(git rev-parse --show-toplevel)" && git add x', "cd $D; git add -A"):
                self.assertIn("cannot be checked", self.bash(hat, command), (hat, command))
            self.assertIsNone(self.bash(hat, "cd $D && git log"), hat)
            self.assertIsNone(self.bash(hat, f"cd $D && git -C {self.evidence} commit -m x"), hat)
        self.assertIsNone(self.bash("build", "cd $D && git commit -m x"))

    def test_review_and_qa_write_only_their_plan_findings_and_status_log_in_the_profile(self):
        briefs, status = self.profile / "briefs", self.profile / ".cache" / "status"
        with mock.patch.dict(os.environ, {"ZED_SESSION": "S76"}):
            for hat in ("review", "qa"):
                for allowed in (briefs / "T28-plan.md", briefs / "T24-rereview3-findings.md", status / "S76.log"):
                    self.assertIsNone(self.call(hat, "Write", file_path=str(allowed)), (hat, allowed))
                    self.assertIsNone(self.bash(hat, f"echo x >> {allowed}"), (hat, allowed))
                for refused in (briefs / "T28-zed.md", status / "S1.log", self.profile / "lessons.md"):
                    self.assertIn("denied", self.call(hat, "Edit", file_path=str(refused)), (hat, refused))
                    self.assertIn("denied", self.bash(hat, f"echo x > {refused}"), (hat, refused))
            self.assertIsNone(self.call("build", "Edit", file_path=str(briefs / "T28-zed.md")))  # build is not limited

    def test_a_read_only_hat_may_not_commit_in_the_checkout_holding_the_profile(self):
        home = self.profile.parent
        for hat in ("review", "qa"):
            for command in (f"git -C {home} commit -m x", f"git -C {self.profile} add briefs/x.md", f"cd {home} && git commit -m x"):
                self.assertIn("denied", self.bash(hat, command), (hat, command))
            self.assertIsNone(self.bash(hat, f"git -C {home} log"), hat)
            self.assertIsNone(self.bash(hat, f"git -C {self.evidence} commit -m x"), hat)
        self.assertIsNone(self.bash("build", f"git -C {home} commit -m x"))

    def test_a_folder_of_the_checkout_holding_the_profile_counts_as_holding_it(self):
        home = self.profile.parent
        (home / "bin").mkdir()
        subprocess.run(["git", "-C", str(home), "init", "-q"], check=True)
        for hat in ("review", "qa"):
            self.assertIn("denied", self.bash(hat, f"git -C {home / 'bin'} commit -am x"), hat)
            self.assertIn("denied", self.bash(hat, f"cd {home / 'bin'} && git commit -am x"), hat)
            self.assertIsNone(self.bash(hat, f"git -C {home / 'bin'} log"), hat)

    def test_raising_a_pr_waits_for_the_merge_gate(self):
        commands = ("gh pr create --draft --title x", "/opt/bin/gh pr create --draft", "git push && gh pr create")
        with mock.patch.object(zeddeny.zedgates, "gate_reason", return_value="no review verdict recorded") as gate:
            for command in commands:
                self.assertIn("waits for a clean review and qa", self.bash("build", command), command)
            gate.assert_called_with(self.wt)
            self.assertIsNone(self.bash("build", "gh pr view 3"))
            self.assertIsNone(self.bash("build", "echo gh pr create"))
        with mock.patch.object(zeddeny.zedgates, "gate_reason", return_value=None):
            for command in commands:
                self.assertIsNone(self.bash("build", command), command)

    def test_the_gate_reason_comes_from_zed_gate_check(self):
        with mock.patch.object(zeddeny.zedgates.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="merge gate: no qa verdict")
            self.assertEqual(zeddeny.zedgates.gate_reason(self.wt), "merge gate: no qa verdict")
            self.assertEqual(run.call_args.args[0][2:5], ["gate", "check", "HEAD"])
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            self.assertIsNone(zeddeny.zedgates.gate_reason(self.wt))

    def test_a_variable_repo_after_dash_c_cannot_be_checked_for_a_read_only_hat(self):
        for hat in ("review", "qa"):
            for command in ("git -C $E commit -m x", "git -C ${E} commit -m x", "cd $D && git -C $E commit -m x"):
                self.assertIn("cannot be checked", self.bash(hat, command), (hat, command))
            self.assertIsNone(self.bash(hat, "git -C $E log"), hat)
            self.assertIn("denied", self.bash(hat, f"git -C {self.wt} commit -m x"), hat)
        self.assertIsNone(self.bash("build", "git -C $E commit -m x"))

    def test_a_compound_opener_or_bare_cd_does_not_hide_the_cd(self):
        emp_dir = self.profile / "briefs"
        for command in (f"{{ cd {emp_dir}; touch sub/x; }}", f"if true; then cd {emp_dir}; touch sub/x; fi"):
            self.assertIn("denied", self.bash("review", command), command)
        self.assertIn("cannot be checked", self.bash("review", "cd && touch x"))

    def test_a_command_ending_in_a_shell_keyword_or_a_semicolon_is_checked_not_crashed(self):
        for command in ("for c in a b; do", "while true; do", "if true; then", "echo x;", "echo x; do", "ls ; ; then"):
            for hat in ("build", "review"):
                self.assertIsNone(self.bash(hat, command), command)

    def test_a_hat_session_may_not_set_or_unset_the_variables_that_say_which_session_it_is(self):
        spoofs = ("ZED_SESSION=S3 zed-employee finish --verdict clean", "FOO=1 ZED_HAT=review ls",
                  "env -u ZED_SESSION zed-employee finish --session S3", "env ZED_PROFILE_DIR=/tmp/p ls",
                  "unset ZED_SESSION; zed-employee finish", "export ZED_SESSION=S3", "export -n HERDR_PANE_ID",
                  "declare -x ZED_HAT=review", "readonly ZED_SESSION=S3", "bash -c 'ZED_SESSION=S3 zed-employee finish'",
                  'sh -c "unset HERDR_PANE_ID; zed-employee finish"', "bash -lc 'env -u ZED_HAT ls'",
                  "eval 'export ZED_SESSION=S3'", "command env -u ZED_SESSION ls", "nohup env ZED_HAT=qa ls",
                  "cd /tmp && ZED_SESSION=S3 ls", "if true; then unset ZED_SESSION; fi")
        clears = ("env -i zed-employee finish --session S3", "env - ls", "env -iu X ls")
        for hat in ("build", "review", "qa"):
            for command in spoofs:
                self.assertIn("tell Zed which session", self.bash(hat, command) or "", f"{hat}: {command}")
            for command in clears:
                self.assertIn("clears the variables", self.bash(hat, command) or "", f"{hat}: {command}")

    def test_the_compact_and_wrapped_spellings_of_stripping_an_identity_are_refused(self):
        strip = ("env -uZED_SESSION -uZED_HAT -uHERDR_PANE_ID bin/zed-employee finish --as-zed --session S2 --verdict clean",
                 "env -uZED_SESSION ls", "env --unset=ZED_SESSION ls", "env -u $V ls", "unset $V", "V=ZED_SESSION; unset $V",
                 "xargs env -u ZED_SESSION ls", "xargs -I{} env -uZED_SESSION {}", "find . -exec env -u ZED_SESSION {} \\;",
                 "sudo env -u ZED_SESSION ls", "timeout 5 env -uHERDR_PANE_ID ls", "sudo bash -c 'unset ZED_HAT'",
                 "find . -exec sh -c 'unset ZED_HAT' \\;", "env -iuX ls", "ZED_SESSION=S3 sudo ls")
        as_zed = ("bin/zed-employee finish --verdict clean --session S3 --as-zed", "zed-employee finish --as-zed --session S3",
                  "zed-employee finish --as=1 --session S3", "zed-employee finish --as --session S3", "xargs zed-employee finish --as-zed",
                  "bash -c 'zed-employee finish --as-zed --session S3'", "env X=1 python3 bin/zed-employee finish --as-zed --session S3")
        for hat in ("build", "review", "qa"):
            for command in strip + as_zed:
                self.assertTrue(self.bash(hat, command), f"{hat}: {command}")

    def test_a_name_the_shell_expands_and_a_wrapper_with_its_own_flags_cannot_hide_a_strip(self):
        indirect = ("V=HERDR_PANE_ID; A=ZED_SESSION; B=ZED_HAT; export $V=w3:p1 $A=S3 $B=qa; zed-employee finish --verdict clean",
                    "export $V=x", "declare $V=x", "typeset -x $V=x", "readonly ${V}=1", "local $V=1", "export `echo ZED_HAT`=x",
                    "unset ZED_{SESSION,HAT}", "export {ZED_SESSION,ZED_HAT}=x", "env $V=x ls", "env -S 'zed-employee finish --as-zed'")
        wrapped = ("nice -n 5 env -u ZED_SESSION ls", "time -p env -uZED_SESSION ls", "command -p env -uZED_SESSION ls",
                   "exec -a x env -u ZED_SESSION ls", "busybox env -u ZED_SESSION ls", "script -q /dev/null env -u ZED_SESSION ls",
                   "nice -n 5 bash -c 'unset ZED_HAT'", "fish -c 'set -e ZED_SESSION'", "set -e ZED_HAT", "nice -n 5 zed-employee finish --as-zed")
        for hat in ("build", "review", "qa"):
            for command in indirect + wrapped:
                self.assertTrue(self.bash(hat, command), f"{hat}: {command}")

    def test_valid_commands_near_the_identity_rules_still_pass(self):
        for command in ("zed-employee finish --verdict clean", "zed-employee finish --draft f.md --verdict blockers", "env FOO=1 ls",
                        "env -u FOO ls", "unset FOO", "export PATH=$PATH:/x", "xargs ls", "sudo ls", "find . -name x -exec ls {} \\;",
                        "grep -r ZED_SESSION bin", "env | grep ZED", "for c in a b; do", "echo x;", "env FOO=1 ls $X", "env FOO=$X ls",
                        "export A=1 B=$(pwd)", "export FOO=\"$HOME/x\"", "set -euo pipefail", "nice -n 5 ls", "time -p ls", "command -p ls"):
            self.assertIsNone(self.bash("build", command), command)

    def test_reading_the_identity_variables_is_not_tampering(self):
        for command in ("git grep -n 'ZED_SESSION=' bin", "grep ZED_HAT bin/zed", "echo $ZED_SESSION", "printenv ZED_SESSION",
                        "FOO=1 ls", "bash -c 'ls'", "env FOO=1 ls", "bash script.sh"):
            self.assertIsNone(self.bash("review", command), command)

    def test_more_cd_spellings_move_the_cwd(self):
        emp_dir = self.profile / "briefs"
        for command in (f"cd -P {emp_dir} && touch sub/x", f"builtin cd {emp_dir} && touch sub/x",
                        f"command cd {emp_dir} && touch sub/x", f"cd -L -- {emp_dir} && touch sub/x"):
            self.assertIn("denied", self.bash("review", command), command)
        for command in ("popd && touch x", f"CDPATH={emp_dir} cd sub && touch x"):
            self.assertIn("cannot be checked", self.bash("review", command), command)

    def test_an_internal_error_denies(self):
        with mock.patch.object(zeddeny, "shell_problem", side_effect=TypeError("boom")):
            self.assertIn("could not be checked", self.bash("review", "git status"))

    def test_targets_the_shell_would_expand_cannot_be_checked(self):
        for command in ("touch $D/x", "echo hi > `pwd`/x", "touch ~root/x"):
            self.assertIn("cannot be checked", self.bash("build", command), command)
        self.assertIsNone(self.bash("build", "echo $HOME > src/a.txt"))  # only the target matters
        self.assertIsNone(self.bash("build", "touch ~/x"))

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

    def test_opencode_v2_tool_names_and_patch_text_are_checked(self):
        src = str(self.wt / "src" / "a.py")
        for tool in ("edit", "write"):  # OpenCode V2 names its tools in lower case and its file `path`
            self.assertIn("denied", self.call("review", tool, path=src))
            self.assertIn("denied", self.call("review", tool, path="src/a.py"))  # relative to the session's directory
            self.assertIsNone(self.call("build", tool, path=src))
        self.assertIn("denied", self.call("review", "patch", patchText=f"*** Begin Patch\n*** Update File: {src}\n@@\n-a\n+b\n*** End Patch"))
        self.assertIn("denied", self.call("qa", "patch", patchText=f"*** Add File: {self.wt}/new.py\n+x\n"))
        self.assertIn("denied", self.call("review", "patch", patchText=f"*** Add File: {self.evidence}/ok.md\n+x\n*** Delete File: {src}\n"))
        self.assertIsNone(self.call("review", "patch", patchText=f"*** Add File: {self.evidence}/ok.md\n+x\n"))
        self.assertIsNone(self.call("build", "patch", patchText=f"*** Update File: {src}\n"))
        self.assertIn("denied", self.call("review", "shell", command="gh pr comment 1"))
        self.assertIn("denied", self.call("review", "shell", command="git commit -m x"))
        self.assertIsNone(self.call("review", "shell", command="git log --oneline"))
        self.assertIn("no subagents", self.call("build", "subagent", prompt="x"))

    def test_opencode_shell_workdir_is_where_relative_targets_resolve(self):
        self.assertIn("denied", self.call("review", "shell", command="rm a.py", workdir="src"))
        self.assertIn("denied", self.call("review", "shell", command="rm a.py", workdir=str(self.wt / "src")))
        self.assertIsNone(self.call("review", "shell", command="rm a.md", workdir=str(self.evidence)))

    def test_the_canary_command_is_denied_to_every_hat(self):
        for hat in ("build", "review", "qa"):
            for tool in ("shell", "Bash"):
                self.assertIn("canary: `zed-deny-canary n1` is denied", self.call(hat, tool, command=" zed-deny-canary n1"))
        self.assertIsNone(self.call("build", "shell", command="echo zed-deny-canary"))

    def test_claude_settings_deny_exactly_the_tools_given(self):
        self.assertEqual(zeddeny.claude_settings(HOME)["permissions"]["deny"], [])  # core names no vendor's tools
        tools = ["mcp__tracker__createIssue", "mcp__wiki__updatePage"]
        self.assertEqual(zeddeny.claude_settings(HOME, tools)["permissions"]["deny"], tools)

    def test_glob_regex(self):
        self.assertTrue(zeddeny.glob_regex("**").match("a/b/c.py"))
        self.assertTrue(zeddeny.glob_regex("briefs/**").match("briefs/sub/x.md"))
        self.assertFalse(zeddeny.glob_regex("briefs/**").match("config/x.md"))
        self.assertFalse(zeddeny.glob_regex("*.py").match("a/b.py"))
        self.assertTrue(zeddeny.glob_regex("*.py").match("b.py"))


@unittest.skipUnless(shutil.which("node"), "node is needed to run the OpenCode plugin")
class OpenCodePluginTests(unittest.TestCase):
    """The generated plugin, run for real under node with the hook object OpenCode would call it with."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / "wt").mkdir()
        self.env = {"ZED_HAT": "review", "ZED_WORKTREE": str(self.root / "wt"), "ZED_PROFILE_DIR": str(self.root / "profile")}
        stub = self.root / "cfg" / "node_modules" / "@opencode" / "plugin"  # OpenCode supplies this package itself
        stub.mkdir(parents=True)
        (stub / "package.json").write_text('{"name": "@opencode/plugin", "type": "module", "main": "index.js"}')
        (stub / "index.js").write_text("export const Plugin = { define: (definition) => definition };\n")
        (self.root / "cfg" / "package.json").write_text('{"type": "module"}')

    def before(self, tool, tool_input, env=None, deny_tools=(), hook=HOME):
        """What the V2 `execute.before` hook does with one tool call: the plugin is set up with the context OpenCode
        v2.2.7 gives it (`ctx.tool.hook`, `ctx.location.directory`) and called with that version's event shape."""
        plugin = zeddeny.opencode_plugin(self.root / "cfg", hook, self.env if env is None else env, deny_tools)
        plugin = plugin / "plugins" / "zed-deny" / "index.js"
        script = (f'import plugin from {json.dumps(plugin.as_uri())};\n'
                  f'const hooks = {{}};\n'
                  f'await plugin.setup({{ location: {{ directory: {json.dumps(str(self.root / "wt"))} }}, '
                  f'tool: {{ hook: async (name, callback) => {{ hooks[name] = callback; }} }}, '
                  f'shell: {{ hook: async (name, callback) => {{ hooks["shell." + name] = callback; }} }} }});\n'
                  + (f'const event = {{ command: {json.dumps(tool_input.get("command", ""))}, cwd: {json.dumps(tool_input.get("cwd", str(self.root / "wt")))}, '
                     f'timeout: 1, shell: "/bin/zsh", env: {{ OPENCODE_PASSWORD: "secret", KEPT: "1" }} }};\n'
                     f'try {{ await hooks["shell.create.before"](event); console.log(plugin.id + " allowed " + JSON.stringify(event.env)); }}\n'
                     if tool == "(server shell)" else
                     f'try {{ await hooks["execute.before"]({{ tool: {json.dumps(tool)}, sessionID: "ses_1", agent: "build", '
                     f'input: {json.dumps(tool_input)} }}); console.log(plugin.id + " allowed"); }}\n')
                  + f'catch (e) {{ console.log("blocked: " + e.message); }}\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith("ZED_")}  # the server's own environment names no hat
        r = subprocess.run(["node", "--input-type=module", "-e", script], env=env, capture_output=True, text=True)
        return r.stdout.strip() or r.stderr.strip()

    def test_a_denied_call_throws_the_reason_and_an_allowed_one_passes(self):
        self.assertIn("blocked: zed-deny: editing", self.before("edit", {"path": str(self.root / "wt" / "a.py")}))
        self.assertIn("blocked: zed-deny: editing", self.before("write", {"path": "a.py", "content": "x"}))
        self.assertIn("blocked: zed-deny: `gh pr comment` is denied", self.before("shell", {"command": "gh pr comment 1"}))
        self.assertIn("blocked: zed-deny: canary: `zed-deny-canary n1`", self.before("shell", {"command": "zed-deny-canary n1"}))
        self.assertEqual(self.before("shell", {"command": "ls"}), "zed.deny allowed")
        self.assertEqual(self.before("edit", {"path": str(self.root / "wt" / "a.py")}, env={**self.env, "ZED_HAT": "build"}),
                         "zed.deny allowed")

    def test_a_shell_the_server_starts_for_any_caller_is_checked_and_never_holds_the_password(self):
        # OpenCode v2.2.7: `POST /api/session/<id>/shell` and `POST /api/shell` pass this hook and no tool hook
        self.assertEqual(self.before("(server shell)", {"command": "ls"}), 'zed.deny allowed {"KEPT":"1"}')
        self.assertIn("blocked: zed-deny: `gh pr comment` is denied", self.before("(server shell)", {"command": "gh pr comment 1"}))
        inside = self.before("(server shell)", {"command": "touch bypass.txt"})
        self.assertIn("blocked: zed-deny:", inside)
        self.assertIn("bypass.txt", inside)
        self.assertIn("blocked: zed-deny:", self.before("(server shell)", {"command": "touch x", "cwd": str(self.root / "wt" / "sub")}))
        self.assertEqual(self.before("(server shell)", {"command": "touch /tmp/x-outside"}), 'zed.deny allowed {"KEPT":"1"}')

    def test_tools_no_worker_may_call_are_denied_by_name(self):
        self.assertEqual(self.before("tracker_createIssue", {}, deny_tools=["tracker_createIssue"]),
                         "blocked: zed-deny: the tool tracker_createIssue is denied to a worker")
        self.assertEqual(self.before("read", {"path": "a.py"}, deny_tools=["tracker_createIssue"]), "zed.deny allowed")

    def test_a_guard_that_cannot_run_denies(self):
        self.assertIn("blocked: ", self.before("shell", {"command": "ls"}, hook=self.root / "no-such-zed-home"))

    def test_the_v1_export_is_gone(self):
        text = (zeddeny.opencode_plugin(self.root / "cfg", HOME, self.env) / "plugins" / "zed-deny" / "index.js").read_text()
        self.assertNotIn("tool.execute.before", text)  # V2 ignores it; V1 servers are not supported
        self.assertFalse((self.root / "cfg" / "plugins" / "zed-deny.js").exists())


class HookScriptTests(unittest.TestCase):
    def run_hook(self, payload, env, *argv):
        with patch.dict(os.environ, env, clear=False), patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
                redirect_stderr(io.StringIO()) as err:
            return deny.main(list(argv)), err.getvalue()

    def test_exit_2_with_the_reason_on_stderr_for_a_denied_call(self):
        env = {"ZED_HAT": "review", "ZED_WORKTREE": "/nonexistent-wt"}
        code, err = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "gh pr comment 1"}}, env)
        self.assertEqual(code, 2)
        self.assertIn("zed-deny: `gh pr comment` is denied", err)

    def test_allowed_calls_and_sessions_without_a_hat_pass(self):
        env = {"ZED_HAT": "review", "ZED_WORKTREE": "/nonexistent-wt"}
        self.assertEqual(self.run_hook({"tool_name": "Bash", "tool_input": {"command": "git log"}}, env)[0], 0)
        plain = {"tool_name": "Bash", "tool_input": {"command": "gh pr comment 1"}}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ZED_HAT", None)
            self.assertEqual(self.run_hook(plain, {})[0], 0)

    def test_unknown_hat_allows_but_garbage_input_denies(self):
        env = {"ZED_HAT": "juggle"}
        self.assertEqual(self.run_hook({"tool_name": "Bash", "tool_input": {"command": "gh pr comment 1"}}, env)[0], 0)
        with patch.dict(os.environ, {"ZED_HAT": "review"}), patch.object(sys, "stdin", io.StringIO("{oops")), redirect_stderr(io.StringIO()):
            self.assertEqual(deny.main([]), 2)

    def test_explain_prints_the_decision(self):
        env = {"ZED_HAT": "review", "ZED_WORKTREE": "/nonexistent-wt"}
        out = io.StringIO()
        with patch("sys.stdout", out):
            code, _ = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "gh pr comment 1"}}, env, "--explain")
        self.assertEqual(code, 0)
        self.assertIn("deny: `gh pr comment`", out.getvalue())


if __name__ == "__main__":
    unittest.main()
