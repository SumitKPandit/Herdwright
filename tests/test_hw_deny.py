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

import hwdeny
import hwemployee as emp
from grammar_cases import ATTACHED_VALUES, REFUSED, WORKTREE_WRITES, form_lines, refused_lines
from real_shell import Scratch

HOME = Path(__file__).resolve().parents[1]
deny = _load.load("hw-deny")


class DecideTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.wt, self.profile, self.evidence = root / "wt", root / "profile", root / "evidence"
        for d in (self.wt / "src", self.profile / "briefs" / "sub", self.evidence):
            d.mkdir(parents=True)
        self.manifests = {h: emp.load_manifest(h) for h in emp.hat_names()}
        # "loose": a hat that may edit some paths only. Its command lines are read loosely, as build's are
        # (hwshell.loose_tokens), so the reading of subshells, openers and here-documents shows in what it is denied.
        review = self.manifests["review"]
        self.loose = {**review, "deny": {**review["deny"], "edit": ["*", "*/**", "profile/**"]}}

    def call(self, hat, tool, **inp):
        payload = {"tool_name": tool, "tool_input": inp, "cwd": str(self.wt)}
        return hwdeny.decide(payload, self.loose if hat == "loose" else self.manifests[hat], self.wt, self.profile)

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
                            "FOO=1 gh pr comment 1", "echo a && gh pr review 1"):
                self.assertIn("denied to this hat", self.bash(hat, command), (hat, command))
            self.assertIn("denied to", self.bash(hat, "(gh pr comment 1)"), hat)  # to this hat, or as a line outside the grammar
            self.assertIsNone(self.bash(hat, "gh pr view 12"), hat)
            self.assertIsNone(self.bash(hat, "echo gh pr comment"), hat)

    def test_cd_in_the_command_line_moves_where_relative_targets_resolve(self):
        emp_dir = self.profile / "briefs"
        for command in (f"cd {emp_dir}/sub && touch x", f"cd {emp_dir} && touch sub/x", f"cd {emp_dir} && echo y > sub/x"):
            self.assertIn("denied", self.bash("review", command), command)
        self.assertIsNone(self.bash("review", f"cd {self.evidence} && touch x"))  # was a false deny
        self.assertIn("denied", self.bash("review", f"cd {self.evidence} && cd {self.wt}/src && touch x"))
        self.assertIsNone(self.bash("build", "cd src && touch x"))

    def test_a_cd_the_shell_expands_leaves_relative_targets_unchecked(self):
        emp_dir = self.profile / "briefs"
        for command in (f"cd $D && touch x", f"D={emp_dir}/sub; cd $D && touch x", f'cd "$E" && touch x',
                        f"cd -- {emp_dir} && touch sub/x", f"pushd {emp_dir} && touch sub/x", "cd - && touch x"):
            self.assertIn("denied" if "--" in command or "pushd" in command else "cannot be checked", self.bash("loose", command), command)
            self.assertIn("denied to a hat that does not edit", self.bash("review", command), command)  # only `cd <path>` is followed
        self.assertIsNone(self.bash("loose", f"cd $D && touch {self.evidence}/x"))  # absolute targets stay checkable

    def test_git_writes_after_an_unknown_cd_are_denied_for_a_read_only_hat(self):
        for hat in ("review", "qa"):
            for command in ("cd $D && git commit -m x", "cd $D && git push", "cd - && git commit -m x",
                            'cd "$(git rev-parse --show-toplevel)" && git add x', "cd $D; git add -A"):
                self.assertIn("cannot be checked", self.bash(hat, command), (hat, command))
            self.assertIn("cannot be checked", self.bash(hat, "cd $D && git log"), hat)  # a `cd` the shell expands is refused whatever follows
            self.assertIsNone(self.bash(hat, f"cd {self.evidence} && git -C {self.evidence} commit -m x"), hat)
        self.assertIsNone(self.bash("build", "cd $D && git commit -m x"))

    def test_review_and_qa_write_only_their_plan_findings_and_status_log_in_the_profile(self):
        briefs, status = self.profile / "briefs", self.profile / ".cache" / "status"
        with mock.patch.dict(os.environ, {"HW_SESSION": "S76"}):
            for hat in ("review", "qa"):
                for allowed in (briefs / "T28-plan.md", briefs / "T24-rereview3-findings.md", status / "S76.log"):
                    self.assertIsNone(self.call(hat, "Write", file_path=str(allowed)), (hat, allowed))
                    self.assertIsNone(self.bash(hat, f"echo x >> {allowed}"), (hat, allowed))
                for refused in (briefs / "T28-hw.md", status / "S1.log", self.profile / "lessons.md"):
                    self.assertIn("denied", self.call(hat, "Edit", file_path=str(refused)), (hat, refused))
                    self.assertIn("denied", self.bash(hat, f"echo x > {refused}"), (hat, refused))
            self.assertIsNone(self.call("build", "Edit", file_path=str(briefs / "T28-hw.md")))  # build is not limited

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
        with mock.patch.object(hwdeny.hwgates, "gate_reason", return_value="no review verdict recorded") as gate:
            for command in commands:
                self.assertIn("waits for a clean review and qa", self.bash("build", command), command)
            gate.assert_called_with(self.wt)
            self.assertIsNone(self.bash("build", "gh pr view 3"))
            self.assertIsNone(self.bash("build", "echo gh pr create"))
        with mock.patch.object(hwdeny.hwgates, "gate_reason", return_value=None):
            for command in commands:
                self.assertIsNone(self.bash("build", command), command)

    def test_the_gate_reason_comes_from_hw_gate_check(self):
        with mock.patch.object(hwdeny.hwgates.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="", stderr="merge gate: no qa verdict")
            self.assertEqual(hwdeny.hwgates.gate_reason(self.wt), "merge gate: no qa verdict")
            self.assertEqual(run.call_args.args[0][2:5], ["gate", "check", "HEAD"])
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            self.assertIsNone(hwdeny.hwgates.gate_reason(self.wt))

    def test_a_variable_repo_after_dash_c_cannot_be_checked_for_a_read_only_hat(self):
        for hat in ("review", "qa"):
            for command in ("git -C $E commit -m x", "git -C ${E} commit -m x", "cd $D && git -C $E commit -m x"):
                self.assertIn("cannot be checked", self.bash(hat, command), (hat, command))
            self.assertIn("cannot be checked", self.bash(hat, "git -C $E log"), hat)  # each word of a hat's git is written out
            self.assertIn("denied", self.bash(hat, f"git -C {self.wt} commit -m x"), hat)
        self.assertIsNone(self.bash("build", "git -C $E commit -m x"))

    def test_a_compound_opener_or_bare_cd_does_not_hide_the_cd(self):
        emp_dir = self.profile / "briefs"
        for command in (f"{{ cd {emp_dir}; touch sub/x; }}", f"if true; then cd {emp_dir}; touch sub/x; fi"):
            self.assertIn("denied", self.bash("loose", command), command)
        self.assertIsNone(self.bash("loose", "cd && touch x"))  # a bare `cd` leads home, outside the worktree
        self.assertIn("cannot be checked", self.bash("loose", f"pushd {self.wt} && pushd {self.evidence} && pushd && touch x"))

    def test_an_opener_or_a_wrapper_before_a_write_does_not_hide_it(self):
        """The worker guard looked past `{`, `then`, `do`, `command`; not past `env`, `sudo`, `xargs`, `!`, `time`."""
        for command in ("{ touch a.py; }", "if true; then touch a.py; fi", "for f in a b; do rm a.py; done", "while true; do rm a.py; done",
                        "command touch a.py", "builtin command touch a.py", "env touch a.py", "env FOO=1 touch a.py", "sudo rm a.py",
                        "sudo -u bob rm a.py", "xargs rm a.py", "! rm a.py", "time rm a.py", "nice -n 5 touch a.py", "FOO=1 nohup touch a.py",
                        "timeout 5 rm a.py", "timeout -k 1 5s rm a.py", "stdbuf -oL touch a.py", "true;>a.py", "find -L . -name x -delete"):
            self.assertIn("denied", self.bash("loose", command), command)
            self.assertIn("denied", self.bash("review", command), command)
        for command in ("env gh pr comment 1 -b x", "if true; then gh pr comment 1 -b x; fi"):
            self.assertIn("anything outward", self.bash("loose", command), command)
        self.assertIn("anything outward", self.bash("review", "env gh pr comment 1 -b x"))
        self.assertIsNone(self.bash("loose", "if true; then ls; fi"))
        for command in ("env FOO=1 ls a.py", "command -v touch", f"env touch {self.evidence}/a.txt"):
            self.assertIsNone(self.bash("loose", command), command)
            self.assertIsNone(self.bash("review", command), command)

    def test_every_redirect_and_pipe_operator_is_read(self):
        """`&>`, `&>>`, `>&` and `|&` stayed inside a command's words, so the write after them was not seen."""
        for command in ("echo x &> a.py", "echo x &>> a.py", "echo x >& a.py", "ls |& tee a.py", "ls |& (cd src && tee a.py)"):
            self.assertIn("denied to this hat", self.bash("loose", command), command)
        for command in ("echo x >&2", "echo x 2>&1", "ls 2>&1 | cat", "echo x >&-", f"echo x &> {self.evidence}/out.txt"):
            self.assertIsNone(self.bash("loose", command), command)
        # a hat that does not edit: the redirects of the grammar, each read
        for command in ("echo x > a.py", "echo x >> a.py", "echo x 2> a.py", "echo x 2>> a.py", "> a.py", "ls 2>&1 > a.py", "ls | tee a.py"):
            self.assertIn("denied to this hat", self.bash("review", command), command)
        for command in ("echo x >&2", "echo x 2>&1", "ls 2>&1 | cat", "cat < a.py", f"echo x > {self.evidence}/out.txt 2>&1"):
            self.assertIsNone(self.bash("review", command), command)

    def test_writes_that_are_not_a_file_writers_first_word(self):
        """A heredoc with the redirect after it, git clean and checkout, chmod, dd and a deleting find passed."""
        for command in ("cat <<EOF > a.py\nx\nEOF", "cat <<'EOF' >> a.py\nx\nEOF\nls", "chmod +x a.py", "chmod -R 755 src", "chown bob a.py",
                        "dd if=/dev/zero of=a.py", "find . -name '*.py' -delete", "find src -exec rm {} \\;", "find . -exec sed -i s/a/b/ {} +"):
            self.assertIn("denied", self.bash("loose", command), command)
            self.assertIn("denied", self.bash("review", command), command)
        for command in ("git clean -fd", "git checkout -- .", "git switch main"):
            self.assertIn("this hat does not change the repo", self.bash("review", command), command)
        for command in (f"cat <<EOF > {self.evidence}/a.txt\nrm a.py\nEOF", f"chmod +x {self.evidence}/run.sh", f"dd if=a.py of={self.evidence}/a",
                        "find . -name '*.py'", "find . -exec grep -l x {} \\;", f"find {self.evidence} -delete", "cat <<EOF\n> a.py\nEOF"):
            self.assertIsNone(self.bash("loose", command), command)
        for command in (f"chmod +x {self.evidence}/run.sh", f"dd if=a.py of={self.evidence}/a", "find . -name '*.py'",
                        "find . -exec grep -l x {} \\;", f"find {self.evidence} -delete"):
            self.assertIsNone(self.bash("review", command), command)

    def test_a_command_ending_in_a_shell_keyword_or_a_semicolon_is_checked_not_crashed(self):
        for command in ("for c in a b; do", "while true; do", "if true; then", "echo x;", "echo x; do", "ls ; ; then"):
            for hat in ("build", "loose"):
                self.assertIsNone(self.bash(hat, command), command)
        self.assertIsNone(self.bash("review", "echo x;"))
        self.assertIn("denied", self.bash("review", "for c in a b; do"))  # outside the grammar

    def test_a_hat_session_may_not_set_or_unset_the_variables_that_say_which_session_it_is(self):
        spoofs = ("HW_SESSION=S3 hw-employee finish --verdict clean", "FOO=1 HW_HAT=review ls",
                  "env -u HW_SESSION hw-employee finish --session S3", "env HW_PROFILE_DIR=/tmp/p ls",
                  "unset HW_SESSION; hw-employee finish", "export HW_SESSION=S3", "export -n HERDR_PANE_ID",
                  "declare -x HW_HAT=review", "readonly HW_SESSION=S3", "bash -c 'HW_SESSION=S3 hw-employee finish'",
                  'sh -c "unset HERDR_PANE_ID; hw-employee finish"', "bash -lc 'env -u HW_HAT ls'",
                  "eval 'export HW_SESSION=S3'", "command env -u HW_SESSION ls", "nohup env HW_HAT=qa ls",
                  "cd /tmp && HW_SESSION=S3 ls")
        clears = ("env -i hw-employee finish --session S3", "env - ls", "env -iu X ls")
        for hat in ("build", "review", "qa"):
            for command in spoofs:
                self.assertIn("tell Herdwright which session", self.bash(hat, command) or "", f"{hat}: {command}")
            for command in clears:
                self.assertIn("clears the variables", self.bash(hat, command) or "", f"{hat}: {command}")
        self.assertIn("tell Herdwright which session", self.bash("build", "if true; then unset HW_SESSION; fi"))
        self.assertIn("denied", self.bash("review", "if true; then unset HW_SESSION; fi"))  # as a line outside the grammar

    def test_the_compact_and_wrapped_spellings_of_stripping_an_identity_are_refused(self):
        strip = ("env -uHW_SESSION -uHW_HAT -uHERDR_PANE_ID bin/hw-employee finish --as-hw --session S2 --verdict clean",
                 "env -uHW_SESSION ls", "env --unset=HW_SESSION ls", "env -u $V ls", "unset $V", "V=HW_SESSION; unset $V",
                 "xargs env -u HW_SESSION ls", "xargs -I{} env -uHW_SESSION {}", "find . -exec env -u HW_SESSION {} \\;",
                 "sudo env -u HW_SESSION ls", "timeout 5 env -uHERDR_PANE_ID ls", "sudo bash -c 'unset HW_HAT'",
                 "find . -exec sh -c 'unset HW_HAT' \\;", "env -iuX ls", "HW_SESSION=S3 sudo ls")
        as_hw = ("bin/hw-employee finish --verdict clean --session S3 --as-hw", "hw-employee finish --as-hw --session S3",
                  "hw-employee finish --as=1 --session S3", "hw-employee finish --as --session S3", "xargs hw-employee finish --as-hw",
                  "bash -c 'hw-employee finish --as-hw --session S3'", "env X=1 python3 bin/hw-employee finish --as-hw --session S3")
        for hat in ("build", "review", "qa"):
            for command in strip + as_hw:
                self.assertTrue(self.bash(hat, command), f"{hat}: {command}")

    def test_a_name_the_shell_expands_and_a_wrapper_with_its_own_flags_cannot_hide_a_strip(self):
        indirect = ("V=HERDR_PANE_ID; A=HW_SESSION; B=HW_HAT; export $V=w3:p1 $A=S3 $B=qa; hw-employee finish --verdict clean",
                    "export $V=x", "declare $V=x", "typeset -x $V=x", "readonly ${V}=1", "local $V=1", "export `echo HW_HAT`=x",
                    "unset HW_{SESSION,HAT}", "export {HW_SESSION,HW_HAT}=x", "env $V=x ls", "env -S 'hw-employee finish --as-hw'")
        wrapped = ("nice -n 5 env -u HW_SESSION ls", "time -p env -uHW_SESSION ls", "command -p env -uHW_SESSION ls",
                   "exec -a x env -u HW_SESSION ls", "busybox env -u HW_SESSION ls", "script -q /dev/null env -u HW_SESSION ls",
                   "nice -n 5 bash -c 'unset HW_HAT'", "fish -c 'set -e HW_SESSION'", "set -e HW_HAT", "nice -n 5 hw-employee finish --as-hw")
        for hat in ("build", "review", "qa"):
            for command in indirect + wrapped:
                self.assertTrue(self.bash(hat, command), f"{hat}: {command}")

    def test_valid_commands_near_the_identity_rules_still_pass(self):
        for command in ("hw-employee finish --verdict clean", "hw-employee finish --draft f.md --verdict blockers", "env FOO=1 ls",
                        "env -u FOO ls", "unset FOO", "export PATH=$PATH:/x", "xargs ls", "sudo ls", "find . -name x -exec ls {} \\;",
                        "grep -r HW_SESSION bin", "env | grep HW", "for c in a b; do", "echo x;", "env FOO=1 ls $X", "env FOO=$X ls",
                        "export A=1 B=$(pwd)", "export FOO=\"$HOME/x\"", "set -euo pipefail", "nice -n 5 ls", "time -p ls", "command -p ls"):
            self.assertIsNone(self.bash("build", command), command)

    def test_reading_the_identity_variables_is_not_tampering(self):
        for command in ("git grep -n 'HW_SESSION=' bin", "grep HW_HAT bin/hw", "echo $HW_SESSION", "printenv HW_SESSION",
                        "FOO=1 ls", "env FOO=1 ls", "bash script.sh"):
            self.assertIsNone(self.bash("review", command), command)
        self.assertIsNone(self.bash("build", "bash -c 'ls'"))

    def test_more_cd_spellings_move_the_cwd(self):
        emp_dir = self.profile / "briefs"
        for command in (f"cd -P {emp_dir} && touch sub/x", f"builtin cd {emp_dir} && touch sub/x",
                        f"command cd {emp_dir} && touch sub/x", f"cd -L -- {emp_dir} && touch sub/x"):
            self.assertIn("denied", self.bash("review", command), command)
        for command in ("popd && touch x", f"CDPATH={emp_dir} cd sub && touch x"):
            self.assertIn("cannot be checked", self.bash("review", command), command)

    def test_a_backslash_newline_joins_two_lines_as_the_shell_does(self):
        """A continued line was read as a new command, so the words on it were hidden from the guard."""
        for hat in ("review", "qa"):
            for command in ("touch \\\n src/a.py", "rm -f \\\n src/a.py", "cp x \\\n src/b.py", "echo x \\\n > src/a.py", "git \\\n commit -m x"):
                self.assertIn("denied", self.bash(hat, command), (hat, command))
            self.assertIsNone(self.bash(hat, f"touch \\\n {self.evidence}/x"), hat)
        for hat in ("build", "review", "qa"):
            self.assertIn("denied to this hat", self.bash(hat, "gh pr \\\n comment 1"), hat)
            self.assertIn("denied to this hat", self.bash(hat, "echo a#b; gh pr comment 1"), hat)  # `#` inside a word is no comment
            self.assertIn("denied to", self.bash(hat, 'echo "$(gh pr comment 1)"'), hat)  # to this hat, or as a line outside the grammar
            self.assertIsNone(self.bash(hat, "echo ';' gh pr comment 1"), hat)  # a quoted `;` is a word

    def test_a_read_only_hat_is_refused_what_cannot_be_read_with_certainty(self):
        """A hat that does not edit is held to the grammar the guards read (hwshell.GRAMMAR): every construct outside
        it is refused, whatever it would do, and the refusal names it. `eval`, a here-string into a shell, a
        here-document whose end line is joined by a backslash, `cd -q`, `chdir` and a `cd` in a pipe passed."""
        for hat in ("review", "qa"):
            for construct, piece in REFUSED:
                command = construct.format(w="touch src/a.py")
                with self.subTest(command, hat=hat):
                    reason = self.bash(hat, command)
                    if "tell Herdwright which session" not in reason:  # `export $N=1`: the identity rule answers first
                        self.assertIn("denied to a hat that does not edit: it cannot be checked", reason)
                        self.assertIn(piece, reason)
            for command in ("eval touch src/a.py", "bash <<< 'touch src/a.py'", "source /dev/stdin <<< 'touch src/a.py'",
                            "cat <<EOF\nEO\\\nF\ntouch src/a.py\nEOF", "cd -q src; touch a.py", "chdir src; touch a.py",
                            f"cd /tmp; true | cd {self.wt}; touch a.py", "=touch src/a.py", "if [[ -n 1 ]] touch src/a.py",
                            "emulate sh -c 'touch src/a.py'", "echo `touch src/a.py`", "bash <<< 'git commit -am x'"):
                self.assertIn("denied", self.bash(hat, command), (hat, command))
            self.assertIn("A longer check goes in a script file", self.bash(hat, "for f in a b; do echo $f; done"))  # what to do instead
            for command in ("git log --format=%h main..HEAD | head -5", "python3 -m unittest tests.test_x 2>&1 | tail -3",
                            f"bash {self.evidence}/probe.sh > {self.evidence}/out.txt 2>&1", f"echo \"$HOME\" 'done: x; qa=y' >> {self.evidence}/s.log"):
                self.assertIsNone(self.bash(hat, command), (hat, command))

    def test_no_line_built_from_a_refused_construct_passes_a_read_only_hat(self):
        for command in refused_lines(extra=100):
            self.assertIn("denied", self.bash("review", command) or "", command)

    def test_a_hat_that_edits_is_not_held_to_the_grammar(self):
        """It writes such shell all day: its line is read as far as it can be, and one that cannot be read passes."""
        for command in ("echo `date`", "a=(1 2); echo ${a[@]}", "diff <(ls) <(ls src)", "echo $'x'", "echo 'unbalanced",
                        "for f in a b; do echo $f; done", "cat <<EOF > src/a.py\nx\nEOF", "eval ls", "bash -c 'touch src/a.py'"):
            self.assertIsNone(self.bash("build", command), command)
        self.assertIn("denied to this hat", self.bash("build", "a=(1 2); gh pr comment 1"))

    def test_an_internal_error_denies(self):
        with mock.patch.object(hwdeny, "shell_problem", side_effect=TypeError("boom")):
            self.assertIn("could not be checked", self.bash("review", "git status"))

    def test_targets_the_shell_would_expand_cannot_be_checked(self):
        for command in ("touch $D/x", "echo hi > `pwd`/x", "touch ~root/x"):
            self.assertIn("cannot be checked", self.bash("build", command), command)
        self.assertIsNone(self.bash("build", "echo $HOME > src/a.txt"))  # only the target matters
        self.assertIsNone(self.bash("build", "touch ~/x"))

    def test_a_cd_is_followed_only_as_cd_and_and_at_the_head_of_the_line_into_a_folder_that_is_there(self):
        """A `cd` may fail (a scratch folder not made yet), and the next command then runs in the worktree,
        where the hat stands. The guard asked whether the folder was there of the resolved path, which
        drops `nope/..`, and went back from a link as the kernel does, not as the shell does. Now only `cd <path> &&`
        at the head of the line is followed, the path asked about as written and without a `..`; after any other `cd`
        a relative write and every git command are refused."""
        (self.evidence / "afile").write_text("x")
        (self.evidence / "d" / "deep").mkdir(parents=True)
        (self.evidence / "l").symlink_to("d/deep")
        writes = ("touch src/a.py", "touch x.txt", "echo hi > src/a.py", "rm src/b.py", "sed -i '' s/1/2/ src/b.py", "cp x src/b.py",
                  "git commit -q --allow-empty -m x", "git add -A", "git tag evil", "git -C src commit -m x", "git log --oneline -3")
        for hat in ("review", "qa"):
            for target in ("nope", "afile", "nope/..", "nope/../d", "afile/../d", "l/..", "d/..", "d", "l", ""):
                for join in ("; ", "\n", " || ", " || true; ", " && true; "):  # what follows runs whether or not the `cd` succeeded
                    for write in writes:
                        line = f"cd {self.evidence}/{target}{join}{write}"
                        self.assertIn("cannot be checked", self.bash(hat, line), (hat, line))
            for target in ("nope", "afile", "nope/..", "nope/../d", "afile/../d", "l/..", "d/.."):  # no folder as the shell walks it, or a `..`
                for write in writes:
                    line = f"cd {self.evidence}/{target} && {write}"
                    self.assertIn("cannot be checked", self.bash(hat, line), (hat, line))
            for line in (f"cd /nope/..; touch src/a.py", f"cd {self.evidence}/l; cd ..; touch ../wt/src/a.py", f"cd {self.evidence}/l && cd .. && touch ../wt/src/a.py",
                         f"cd {self.evidence}/l/..; git -C ../wt tag evil", f"ls && cd {self.evidence} && touch x.txt", f"mkdir -p {self.evidence}/n; cd {self.evidence}/n && touch x",
                         f"cd {self.evidence} && ls; cd {self.evidence}/d && touch x", f"cd {self.evidence} && ls || touch x.txt", f"CDPATH={self.wt} && cd src && touch a.py", f"cd {self.evidence} && ls\ngit status"):
                self.assertIn("cannot be checked", self.bash(hat, line), (hat, line))
            for line in (f"cd {self.evidence}/nope; touch {self.evidence}/x", f"cd {self.evidence}/nope && ls src", f"cd {self.evidence}; ls; cat x | head -3",
                         f"cd {self.evidence} && touch x.txt && git init -q && git add -A && git commit -q -m x | cat", f"cd {self.evidence}/l && touch x",
                         f"cd {self.evidence} && cd d && cd deep && echo hi > x 2>&1", f"cd {self.evidence}/d && touch x; ls; echo ok > {self.evidence}/y"):
                self.assertIsNone(self.bash(hat, line), (hat, line))
            self.assertIn("denied", self.bash(hat, f"cd {self.evidence} && cd {self.wt}/src && touch x.txt"), hat)
            self.assertIn("this `cd`", self.bash(hat, "cd +1; touch x"), hat)
        self.assertIsNone(self.bash("build", f"cd {self.evidence}/nope; touch src/a.py"))  # a hat that edits: its worktree is its own

    def test_a_path_through_one_the_line_itself_changes_cannot_be_checked(self):
        """The guard resolves a path before the line runs, so a link made on the line, or a folder it moves,
        leads a later path of the same line into the worktree unseen."""
        root, scratch = self.wt.parent, self.evidence / "d"
        scratch.mkdir()
        for hat in ("review", "qa"):
            for command in (f"ln -s {root} {self.evidence}/k; touch {self.evidence}/k/wt/src/a.py", f"ln -s {root} {self.evidence}/k && echo hi > {self.evidence}/k/wt/x",
                            f"mv {scratch} {self.evidence}/e; rm {self.evidence}/e/../../wt/src/a.py", f"rm -rf {scratch}; cd {scratch}; touch x.txt",
                            f"mv {scratch} {self.evidence}/e; cd {scratch}; git commit -m x", f"ln -sfn {self.wt.parent} {scratch}; git -C {scratch}/wt commit -m x"):
                self.assertIn("cannot be checked", self.bash(hat, command), (hat, command))
            for command in (f"ln -s {self.wt} {self.evidence}/l", f"ln -s ../wt/src {self.evidence}/l", f"cd {self.evidence} && ln -s ../wt/src/a.py l",
                            f"ln {self.wt}/src/a.py {self.evidence}/hard", f"ln -s -t {self.evidence} ../wt/src"):
                self.assertIn("is denied to this hat", self.bash(hat, command), (hat, command))
            for command in (f"mkdir -p {self.evidence}/n && touch {self.evidence}/n/x", f"rm -f {self.evidence}/o; echo hi > {self.evidence}/o",
                            f"rm -rf {scratch} && mkdir -p {scratch} && git clone -q {self.wt} {scratch}/c && git -C {scratch}/c commit --allow-empty -m x",
                            f"cp {self.evidence}/x {self.evidence}/a.py && echo x >> {self.evidence}/a.py", f"ln -s {self.evidence}/a {self.evidence}/b; touch {self.evidence}/c",
                            f"ln -s ../x {self.evidence}/d/l"):  # read from the shell `../x` lies in the worktree; from the link's folder it does not
                self.assertIsNone(self.bash(hat, command), (hat, command))

    def test_of_a_copy_only_the_target_is_a_write_and_find_execdir_is_refused(self):
        """`cp src/b.py <evidence>/copy.py` only reads the worktree and was refused; `find -execdir` runs its
        command in the folder of each file it finds, where the guard did not look."""
        (self.evidence / "d").mkdir()
        for hat in ("review", "qa"):
            self.assertIsNone(self.bash(hat, f"cp src/b.py {self.evidence}/copy.py"), hat)
            self.assertIsNone(self.bash(hat, f"cp -R src {self.evidence}/d"), hat)
            self.assertIn("is denied to this hat", self.bash(hat, f"cp {self.evidence}/copy.py src/b.py"), hat)
            self.assertIn("is denied to this hat", self.bash(hat, f"cp -t src {self.evidence}/copy.py"), hat)
            for run in ("touch ../../wt/src/a.py", "git -C ../../wt tag evil", "ls"):
                for primary in ("-execdir", "-okdir"):
                    self.assertIn(f"`find {primary}`", self.bash(hat, f"find {self.evidence} -name f2.txt {primary} {run} \\;"), (hat, run))

    def test_a_path_in_another_case_is_the_same_path_where_the_disk_does_not_tell_the_cases_apart(self):
        """On a default macOS disk `<tmp>/WT` is `<tmp>/wt`, and the guard compared the two letter by letter."""
        upper = self.wt.parent / "WT"
        if not upper.exists():
            self.skipTest("this disk tells `WT` from `wt`")
        (self.profile / "briefs" / "T1-plan.md").write_text("x")
        for hat in ("review", "qa"):
            for command in (f"touch {upper}/src/a.py", f"echo hi > {upper}/src/a.py", f"rm {upper}/SRC/b.py", f"cd {upper} && touch src/a.py",
                            f"git -C {upper} tag evil", f"touch {str(self.profile).upper()}/config.json"):
                self.assertIn("denied", self.bash(hat, command), (hat, command))
            self.assertIn("denied", self.call(hat, "Edit", file_path=f"{upper}/src/a.py"), hat)
            self.assertIsNone(self.bash(hat, f"echo x >> {self.profile}/BRIEFS/T1-plan.md"), hat)  # what it may write, it may in any case

    def test_git_only_reads_outside_the_scratch_folder_and_a_scratch_clone_keeps_to_it(self):
        """What went wrong before: the hat's git check was a list of the writes somebody named; `GIT_DIR=<worktree>/.git git
        commit` ran from a scratch folder; git in a scratch clone was free, paths and all (`git -C <clone>
        diff --output=<worktree>/a.py` overwrote a source file, `push` wrote the repo behind the worktree). git only
        reads, by the coordinator's allow list, wherever it runs. The one opening is the scratch folder: `git clone`
        and `git init` into it, and git in a repo there that names no path outside it, no program, no `push` and no
        `fetch` or `pull` with a refspec. A line that runs git sets no GIT_* variable."""
        clone, wt = self.evidence / "c", self.wt
        clone.mkdir()
        for hat in ("review", "qa"):
            for command in ("git branch evil", "git update-ref refs/heads/evil HEAD", "git diff --output=src/a.py HEAD", "git config user.name evil",
                            f"git worktree add {self.evidence}/wt", "git read-tree --empty", "git symbolic-ref HEAD refs/heads/other", "git fetch origin",
                            "git -c alias.ci=commit ci -m x", "git -c core.pager=x log", f"cd {self.evidence} && git --git-dir={wt}/.git tag evil",
                            f"git -C {wt}/src stash", f"git -C {self.profile} tag evil", "true; taskpolicy -b git tag evil",
                            "find . -maxdepth 0 -exec git tag evil \\;", "find . -maxdepth 0 -exec touch x.txt \\;",
                            "git clone . c", f"git clone {wt} src/c", f"cd {self.evidence} && git clone {wt} {wt}/c", f"git clone {wt}",
                            "git init", "git init sub", f"git -C {self.evidence} init {wt}/sub", f"cd {self.evidence} && git init -q {wt}/src/sub",
                            # a scratch clone that names a path in the worktree, or a program, or writes another repo
                            f"git -C {clone} diff --output={wt}/src/a.py HEAD", f"cd {clone} && git diff --output=../../wt/src/a.py HEAD",
                            f"git -C {clone} log --output={wt}/src/b.py", f"git -C {clone} config --file {wt}/.git/config user.name evil",
                            f"git -C {clone} worktree add {wt}/wt2", f"git -C {clone} archive -o {wt}/x.tar HEAD", f"git -C {clone} format-patch -1 -o {wt}/p",
                            f"git -C {clone} bundle create {wt}/b HEAD", f"git -C {clone} checkout-index -a -f --prefix={wt}/src/n/",
                            f"git -C {clone} push origin HEAD:refs/heads/evil", f"git -C {clone} push -q {wt} HEAD:refs/heads/evil", f"git -C {clone} push",
                            f"git -C {clone} fetch origin main:evil", f"git -C {clone} pull origin main", f"git -C {clone} fetch -q --upload-pack 'touch x;git-upload-pack' origin",
                            f"git -C {clone} -c core.hooksPath=/dev/null commit --amend -m y", f"git -C {clone} --work-tree={wt} checkout .",
                            f"git -C {clone} config core.pager less", f"git -C {clone} rebase -x 'touch x' main", f"git -C {clone} filter-branch",
                            f"git clone -u 'touch x' {wt} {clone}", f"git clone -c core.fsmonitor=x {wt} {clone}", f"git clone --depth 1 {wt} {clone}",
                            f"git clone --template={wt} {wt} {clone}", f"git -C {clone} init --separate-git-dir={wt}/g", f"git -C {self.evidence} init -q -b main r"):
                self.assertIn("is denied", self.bash(hat, command), (hat, command))
            for command in ("X=--output=src/a.py; git diff $X HEAD", "ls | xargs git log", "xargs git tag", f"git -C $E log", f"git -C {clone} commit -m $M"):
                self.assertIn("cannot be checked", self.bash(hat, command), (hat, command))
            for command in (f"cd {self.evidence} && GIT_DIR={wt}/.git git commit -q --allow-empty -m x", f"export GIT_DIR={wt}/.git; git -C {clone} tag evil",
                            f"env GIT_WORK_TREE={wt} git -C {clone} checkout .", f"GIT_INDEX_FILE=x true; git -C {clone} log"):
                self.assertIn("and runs git", self.bash(hat, command), (hat, command))
            # reads anywhere; and what a review or QA session does in its scratch folder: clone, check out, commit, fetch, run the tests
            for command in ("git log --oneline -3", "git status --short", "git diff main...HEAD -- src", "git -C src show --stat HEAD", "git branch --show-current",
                            f"git clone -q {wt} {clone}", f"git clone --depth=1 {wt} {clone} && git -C {clone} commit --allow-empty -m x",
                            f"cd {self.evidence} && git clone -q {wt} c && git -C c tag x", f"git -C {self.evidence} init -q r", f"git init -q --initial-branch=main {self.evidence}/r",
                            f"cd {self.evidence} && git init -q && git add -A && git commit -m x", f"git -C {clone} checkout -q -b try origin/main",
                            f"git -C {clone} config user.name t && git -C {clone} commit -q -am 'a fix: src/a.py'", f"git -C {clone} fetch -q origin",
                            f"git -C {clone} diff --output={self.evidence}/x.diff HEAD~1", f"git -C {clone} worktree add -q {self.evidence}/w2",
                            f"git -C {clone} reset -q --hard HEAD~1 && git -C {clone} stash", f"cd {clone} && git checkout -q main && python3 -m unittest discover tests",
                            "GIT_CONFIG_GLOBAL=/dev/null python3 -m unittest", "git log --grep=GIT_DIR"):
                self.assertIsNone(self.bash(hat, command), (hat, command))
        for command in ("git branch x", "GIT_DIR=x git commit -m x", "git -c user.name=t commit -m x", "find . -exec git add {} +"):
            self.assertIsNone(self.bash("build", command), command)

    def test_git_in_a_scratch_clone_stays_in_it_by_its_options_too(self):
        """Writes from a scratch clone that name no path on the command line (`config --global` writes
        the user's own git identity, `apply --unsafe-paths` goes where the patch says), and a folder the line itself
        turns into a door to the worktree's repo (a `.git` file written before the git command)."""
        clone, wt, out = self.evidence / "c", self.wt, self.evidence
        git = lambda *words: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *words], cwd=clone, capture_output=True, check=True)
        clone.mkdir()
        git("init", "-q", "-b", "main")
        (out / "plain").mkdir()
        for hat in ("review", "qa"):
            for command in (f"git -C {clone} config --global user.email x", f"git -C {clone} config --system user.name x",
                            f"git -C {clone} config --glo user.email x",
                            f"git -C {clone} config --file={out}/cfg user.name x", f"git -C {clone} config -f {out}/cfg user.name x",
                            f"git -C {clone} apply --unsafe-paths {out}/p.patch", f"git -C {clone} apply --unsafe {out}/p.patch",
                            f"mkdir {out}/gf; echo 'gitdir: {wt}/.git' > {out}/gf/.git; git -C {out}/gf tag evil",
                            f"mkdir {out}/gf && echo 'gitdir: {wt}/.git' > {out}/gf/.git && git -C {out}/gf commit -q --allow-empty -m x"):
                self.assertIn("is denied", self.bash(hat, command), (hat, command))
            self.assertIn("git -C <clone> config user.email <x>", self.bash(hat, f"git -C {clone} config --global user.email x"), hat)
            self.assertIn("a command line of its own", self.bash(hat, f"mkdir {out}/gf; git -C {out}/gf tag evil"), hat)
            # `--worktree` sets the clone's own `config.worktree`
            for command in (f"git -C {clone} config user.email x", f"git -C {clone} config --worktree user.name x", f"git -C {clone} apply {out}/p.patch", f"git -C {clone} commit -q --allow-empty -m x",
                            f"git clone -q {wt} {out}/n && git -C {out}/n commit --allow-empty -m x && git -C {out}/n/src status",
                            f"git init -q {out}/r && git -C {out}/r commit --allow-empty -m x", f"cd {out}/plain && git init -q && git add -A"):
                self.assertIsNone(self.bash(hat, command), (hat, command))

    def test_a_line_that_sets_home_names_no_path_by_a_tilde(self):
        """The guard resolves `~` with the session's home, the shell with the one the line set."""
        wt = self.wt
        for hat in ("review", "qa"):
            for command in (f"HOME={wt}; touch ~/src/a.py", f"export HOME={wt}; echo hi > ~/src/a.py", f"HOME={wt}; rm ~/src/b.py",
                            f"HOME={wt} && cp {self.evidence}/f ~/src/a.py"):
                self.assertIn("sets `HOME`", self.bash(hat, command), (hat, command))
            # bash expands `~` after `=` too (`of=~/x`); such a word is no path written out, with or without `HOME`
            for command in (f"HOME={wt}; dd if={self.evidence}/f of=~/src/a.py", f"HOME={wt}; cp {self.evidence}/f --target-directory=~/src",
                            f"HOME={wt}; echo hi > a=~/b", "dd if=src/a.py of=~/x"):
                self.assertIn("must be written out", self.bash(hat, command), (hat, command))
            for command in (f"HOME={self.evidence} python3 -m unittest", f"HOME={self.evidence}; touch {self.evidence}/x.txt", "ls ~/Documents",
                            f"cat ~/notes.txt > {self.evidence}/n.txt"):
                self.assertIsNone(self.bash(hat, command), (hat, command))

    @unittest.skipUnless(os.path.isdir("/System/Volumes/Data" + os.path.realpath(tempfile.gettempdir())), "needs macOS's data volume")
    def test_the_worktree_by_the_data_volume_s_second_name_is_the_worktree(self):
        """`/System/Volumes/Data<worktree>` is the worktree, by a name `realpath` leaves as it is."""
        other = f"/System/Volumes/Data{self.wt}"
        for hat in ("review", "qa"):
            for command in (f"touch {other}/src/a.py", f"echo hi > {other}/src/b.py", f"rm {other}/src/b.py", f"cd {other} && touch src/a.py",
                            f"cp {self.evidence}/f.txt {other}/src/a.py"):
                self.assertIn("denied", self.bash(hat, command), (hat, command))
            self.assertIn("denied", self.call(hat, "Edit", file_path=f"{other}/src/b.py"), hat)
            self.assertIn("denied", self.call(hat, "Write", file_path=f"{other}/new/b.py"), hat)
            self.assertIsNone(self.bash(hat, f"echo hi > /System/Volumes/Data{self.evidence}/x.txt"), hat)
            self.assertIsNone(self.bash(hat, f"echo x >> /System/Volumes/Data{self.profile}/briefs/T1-plan.md"), hat)

    def test_the_scratch_folder_lies_under_the_temp_directory(self):
        """A repo elsewhere on the disk (the user's other projects) is no scratch clone: git there only reads."""
        elsewhere = Path.home() / "some-project"
        for hat in ("review", "qa"):
            for command in (f"git -C {elsewhere} commit -m x", f"git clone {self.wt} {elsewhere}", f"git init {elsewhere}", f"cd / && git init -q"):
                self.assertIn("outside the scratch folder", self.bash(hat, command), (hat, command))
            self.assertIsNone(self.bash(hat, f"git -C {elsewhere} log -1"), hat)
        self.assertTrue(all(hwdeny.hwshell.inside(self.evidence, hwdeny.scratch_roots(), "/") for _ in "x"))

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_a_hat_of_a_project_without_worktrees_works_in_the_scratch_folder_spawn_handed_it(self):
        """A project without worktrees keeps a session's scratch folder in the project folder
        (`T<id>-<agent>`), where a review, QA or plan session could neither clone nor commit. spawn hands the folder
        to the session (HW_SCRATCH): there the hat writes, clones and commits, on real git; the project folder
        around it, and the project's own repo, stay as they were."""
        wt, scratch = self.wt, self.wt / "T1-qa-t1"
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        run = lambda line: subprocess.run(["bash", "-c", line], cwd=wt, env=env, capture_output=True, text=True)
        (wt / "src" / "a.py").write_text("x = 1\n")
        self.assertEqual(run("git init -q -b main && git add -A && git commit -q -m root").returncode, 0)
        state = lambda: run("git rev-parse HEAD && git for-each-ref && git status --porcelain").stdout
        (wt / "T1-other").mkdir()
        scratch.mkdir()
        before = state()
        works = (f"git clone -q {wt} {scratch}/c", f"git -C {scratch}/c commit -q --allow-empty -m try",
                 f"cd {scratch}/c && git checkout -q -b try && git reset -q --hard HEAD~1", f"git init -q {scratch}/r",
                 f"echo note > {scratch}/notes.txt", f"cp src/a.py {scratch}/a.py", f"rm {scratch}/a.py")
        refused = (f"git -C {scratch} commit -q --allow-empty -m x", f"cd {scratch} && git tag evil", "git commit -q --allow-empty -m x",
                   f"git clone -q {wt} {wt}/T1-other/c", f"git clone -q {wt} {wt}/c", f"git -C {scratch}/c diff --output={wt}/src/a.py HEAD",
                   f"git -C {scratch}/c push -q origin HEAD:refs/heads/evil", "touch src/b.py", f"echo x > {wt}/T1-other/x.txt",
                   f"rm {scratch}/../src/a.py")
        for hat in ("review", "qa", "plan"):
            self.assertIn("is denied", self.bash(hat, works[0]), hat)  # not without the folder spawn hands over
            with patch.dict(os.environ, {"HW_SCRATCH": str(scratch)}):
                for line in works:
                    self.assertIsNone(self.bash(hat, line), (hat, line))
                    done = run(line)
                    self.assertEqual(done.returncode, 0, (line, done.stderr))
                for line in refused:
                    self.assertIn("denied", self.bash(hat, line), (hat, line))
                self.assertIsNone(self.call(hat, "Write", file_path=str(scratch / "c" / "report.md")), hat)
                self.assertIn("denied", self.call(hat, "Edit", file_path=str(wt / "src" / "a.py")), hat)
                self.assertIn("denied", self.call(hat, "Write", file_path=str(wt / "T1-other" / "x.py")), hat)
            self.assertEqual(run(f"git -C {scratch}/c log --format=%s -1").stdout.strip(), "root")  # the clone took the commit and the reset
            shutil.rmtree(scratch)
            scratch.mkdir()
            self.assertEqual(state(), before, hat)  # the project folder and its repo as they were

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_another_worktree_of_the_same_repo_is_not_a_scratch_clone(self):
        """A ref written through the repo's main checkout or another worktree of it is written in the repo under review."""
        git = lambda *words, cwd=self.wt: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *words], cwd=cwd, capture_output=True, check=True)
        git("init", "-q", "-b", "main")
        git("commit", "-q", "--allow-empty", "-m", "root")
        git("worktree", "add", "-q", str(self.evidence / "other"))
        git("clone", "-q", str(self.wt), str(self.evidence / "clone"))
        for hat in ("review", "qa"):
            self.assertIn("is denied", self.bash(hat, f"git -C {self.evidence}/other branch evil"), hat)
            self.assertIn("is denied", self.bash(hat, f"cd {self.evidence}/other && git update-ref refs/heads/main HEAD~1"), hat)
            self.assertIsNone(self.bash(hat, f"git -C {self.evidence}/other log -1"), hat)
            self.assertIsNone(self.bash(hat, f"git -C {self.evidence}/clone branch free"), hat)

    @unittest.skipUnless(all(map(shutil.which, ("git", "bash", "zsh"))), "needs git, bash and zsh")
    def test_on_a_real_shell_what_a_read_only_hat_is_allowed_of_the_grammar_leaves_its_worktree_as_it_was(self):
        """The second table: every form the grammar reads (grammar_cases.FORMS) around every command that
        changes the worktree a review or QA session stands in. Whatever the guard allows of it is run in bash and in
        zsh and must leave the worktree as it was. Before, `cd <not there>; touch x.txt`, `git update-ref` and
        `GIT_DIR=<worktree>/.git git commit` from a scratch folder were allowed and wrote."""
        with tempfile.TemporaryDirectory() as root:
            scratch = Scratch(root)
            lines = [scratch.line(case) for case in form_lines(WORKTREE_WRITES)]
            allowed = [line for line in lines if all(hwdeny.shell_problem(line, self.manifests[hat], scratch.repo, self.profile, str(scratch.repo)) is None
                                                     for hat in ("review", "qa"))]
            for line in allowed:
                for shell in ("bash", "zsh"):
                    with self.subTest(line, shell=shell):
                        self.assertFalse(scratch.reaches(line, shell, cwd="repo"), "an allowed command line changed the worktree")
            self.assertGreater(len(lines), 1000)
            self.assertGreater(len(allowed), 10)  # `cd <scratch> && touch x.txt` writes outside the worktree

    @unittest.skipUnless(all(map(shutil.which, ("git", "bash", "zsh"))), "needs git, bash and zsh")
    def test_on_a_real_shell_a_value_attached_to_a_short_option_runs_in_a_scratch_clone(self):
        """A commit message, a branch name or a path in the scratch folder, written right behind its short
        option with a `/` in it (`-m"fix a/b"`, `-bfeat/x`, `-qo<scratch>/patches`), was refused as a path outside
        the scratch folder. Each line is allowed, runs on real git and leaves the worktree's repo as it was."""
        with tempfile.TemporaryDirectory() as root:
            scratch = Scratch(root)
            for case in ATTACHED_VALUES:
                line = scratch.line(case)
                for hat in ("review", "qa"):
                    self.assertIsNone(hwdeny.shell_problem(line, self.manifests[hat], scratch.repo, self.profile, str(scratch.repo)), (hat, line))
                for shell in ("bash", "zsh"):
                    with self.subTest(line, shell=shell):
                        done = subprocess.run([shell, "-c", line], cwd=scratch.repo, env=scratch.env, capture_output=True, text=True)
                        self.assertEqual(done.returncode, 0, done.stderr)
                        self.assertFalse(scratch.reaches(line, shell, cwd="repo"), "an allowed command line changed the worktree")

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

    def test_a_cd_in_a_subshell_a_pipe_or_the_background_does_not_move_the_commands_after_it(self):
        for command in (f"(cd {self.evidence}) && touch x", f"(cd {self.evidence}; ls); rm src/a.py", f"cd {self.evidence} | touch x",
                        f"cd {self.evidence} & touch x", f"(cd {self.evidence} && (cd /tmp)) ; echo y > x", "(cd /tmp); (touch x)"):
            self.assertIn("denied to this hat", self.bash("loose", command), command)
        for command in (f"(cd {self.evidence} && touch x)", f"(cd {self.evidence}; (cd /tmp; ls); touch x)",
                        f"cd {self.evidence} && (cd {self.wt}; ls) && touch x", f"ls | (cd {self.evidence} && tee x)"):
            self.assertIsNone(self.bash("loose", command), command)
        self.assertIn("denied", self.bash("loose", f"cd {self.evidence} && (cd {self.wt} && touch x)"))
        self.assertIn("denied", self.call("loose", "shell", command="(cd /tmp) && touch a.py", workdir="src"))  # OpenCode

    def test_the_canary_command_is_denied_to_every_hat(self):
        for hat in ("build", "review", "qa"):
            for tool in ("shell", "Bash"):
                self.assertIn("canary: `hw-deny-canary n1` is denied", self.call(hat, tool, command=" hw-deny-canary n1"))
        self.assertIsNone(self.call("build", "shell", command="echo hw-deny-canary"))

    def test_claude_settings_deny_exactly_the_tools_given(self):
        self.assertEqual(hwdeny.claude_settings(HOME)["permissions"]["deny"], [])  # core names no vendor's tools
        tools = ["mcp__tracker__createIssue", "mcp__wiki__updatePage"]
        self.assertEqual(hwdeny.claude_settings(HOME, tools)["permissions"]["deny"], tools)

    def test_glob_regex(self):
        self.assertTrue(hwdeny.glob_regex("**").match("a/b/c.py"))
        self.assertTrue(hwdeny.glob_regex("briefs/**").match("briefs/sub/x.md"))
        self.assertFalse(hwdeny.glob_regex("briefs/**").match("config/x.md"))
        self.assertFalse(hwdeny.glob_regex("*.py").match("a/b.py"))
        self.assertTrue(hwdeny.glob_regex("*.py").match("b.py"))


@unittest.skipUnless(shutil.which("node"), "node is needed to run the OpenCode plugin")
class OpenCodePluginTests(unittest.TestCase):
    """The generated plugin, run for real under node with the hook object OpenCode would call it with."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / "wt").mkdir()
        self.env = {"HW_HAT": "review", "HW_WORKTREE": str(self.root / "wt"), "HW_PROFILE_DIR": str(self.root / "profile")}
        stub = self.root / "cfg" / "node_modules" / "@opencode" / "plugin"  # OpenCode supplies this package itself
        stub.mkdir(parents=True)
        (stub / "package.json").write_text('{"name": "@opencode/plugin", "type": "module", "main": "index.js"}')
        (stub / "index.js").write_text("export const Plugin = { define: (definition) => definition };\n")
        (self.root / "cfg" / "package.json").write_text('{"type": "module"}')

    def before(self, tool, tool_input, env=None, deny_tools=(), hook=HOME):
        """What the V2 `execute.before` hook does with one tool call: the plugin is set up with the context OpenCode
        v2.2.7 gives it (`ctx.tool.hook`, `ctx.location.directory`) and called with that version's event shape."""
        plugin = hwdeny.opencode_plugin(self.root / "cfg", hook, self.env if env is None else env, deny_tools)
        plugin = plugin / "plugins" / "hw-deny" / "index.js"
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
        env = {k: v for k, v in os.environ.items() if not k.startswith("HW_")}  # the server's own environment names no hat
        r = subprocess.run(["node", "--input-type=module", "-e", script], env=env, capture_output=True, text=True)
        return r.stdout.strip() or r.stderr.strip()

    def test_a_denied_call_throws_the_reason_and_an_allowed_one_passes(self):
        self.assertIn("blocked: hw-deny: editing", self.before("edit", {"path": str(self.root / "wt" / "a.py")}))
        self.assertIn("blocked: hw-deny: editing", self.before("write", {"path": "a.py", "content": "x"}))
        self.assertIn("blocked: hw-deny: `gh pr comment` is denied", self.before("shell", {"command": "gh pr comment 1"}))
        self.assertIn("blocked: hw-deny: canary: `hw-deny-canary n1`", self.before("shell", {"command": "hw-deny-canary n1"}))
        self.assertEqual(self.before("shell", {"command": "ls"}), "hw.deny allowed")
        self.assertEqual(self.before("edit", {"path": str(self.root / "wt" / "a.py")}, env={**self.env, "HW_HAT": "build"}),
                         "hw.deny allowed")

    def test_a_shell_the_server_starts_for_any_caller_is_checked_and_never_holds_the_password(self):
        # OpenCode v2.2.7: `POST /api/session/<id>/shell` and `POST /api/shell` pass this hook and no tool hook
        self.assertEqual(self.before("(server shell)", {"command": "ls"}), 'hw.deny allowed {"KEPT":"1"}')
        self.assertIn("blocked: hw-deny: `gh pr comment` is denied", self.before("(server shell)", {"command": "gh pr comment 1"}))
        inside = self.before("(server shell)", {"command": "touch bypass.txt"})
        self.assertIn("blocked: hw-deny:", inside)
        self.assertIn("bypass.txt", inside)
        self.assertIn("blocked: hw-deny:", self.before("(server shell)", {"command": "touch x", "cwd": str(self.root / "wt" / "sub")}))
        self.assertEqual(self.before("(server shell)", {"command": "touch /tmp/x-outside"}), 'hw.deny allowed {"KEPT":"1"}')

    def test_tools_no_worker_may_call_are_denied_by_name(self):
        self.assertEqual(self.before("tracker_createIssue", {}, deny_tools=["tracker_createIssue"]),
                         "blocked: hw-deny: the tool tracker_createIssue is denied to a worker")
        self.assertEqual(self.before("read", {"path": "a.py"}, deny_tools=["tracker_createIssue"]), "hw.deny allowed")

    def test_a_guard_that_cannot_run_denies(self):
        self.assertIn("blocked: ", self.before("shell", {"command": "ls"}, hook=self.root / "no-such-hw-home"))

    def test_the_v1_export_is_gone(self):
        text = (hwdeny.opencode_plugin(self.root / "cfg", HOME, self.env) / "plugins" / "hw-deny" / "index.js").read_text()
        self.assertNotIn("tool.execute.before", text)  # V2 ignores it; V1 servers are not supported
        self.assertFalse((self.root / "cfg" / "plugins" / "hw-deny.js").exists())


class HookScriptTests(unittest.TestCase):
    def run_hook(self, payload, env, *argv):
        with patch.dict(os.environ, env, clear=False), patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), \
                redirect_stderr(io.StringIO()) as err:
            return deny.main(list(argv)), err.getvalue()

    def test_exit_2_with_the_reason_on_stderr_for_a_denied_call(self):
        env = {"HW_HAT": "review", "HW_WORKTREE": "/nonexistent-wt"}
        code, err = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "gh pr comment 1"}}, env)
        self.assertEqual(code, 2)
        self.assertIn("hw-deny: `gh pr comment` is denied", err)

    def test_allowed_calls_and_sessions_without_a_hat_pass(self):
        env = {"HW_HAT": "review", "HW_WORKTREE": "/nonexistent-wt"}
        self.assertEqual(self.run_hook({"tool_name": "Bash", "tool_input": {"command": "git log"}}, env)[0], 0)
        plain = {"tool_name": "Bash", "tool_input": {"command": "gh pr comment 1"}}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HW_HAT", None)
            self.assertEqual(self.run_hook(plain, {})[0], 0)

    def test_unknown_hat_allows_but_garbage_input_denies(self):
        env = {"HW_HAT": "juggle"}
        self.assertEqual(self.run_hook({"tool_name": "Bash", "tool_input": {"command": "gh pr comment 1"}}, env)[0], 0)
        with patch.dict(os.environ, {"HW_HAT": "review"}), patch.object(sys, "stdin", io.StringIO("{oops")), redirect_stderr(io.StringIO()):
            self.assertEqual(deny.main([]), 2)

    def test_explain_prints_the_decision(self):
        env = {"HW_HAT": "review", "HW_WORKTREE": "/nonexistent-wt"}
        out = io.StringIO()
        with patch("sys.stdout", out):
            code, _ = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "gh pr comment 1"}}, env, "--explain")
        self.assertEqual(code, 0)
        self.assertIn("deny: `gh pr comment`", out.getvalue())


if __name__ == "__main__":
    unittest.main()
