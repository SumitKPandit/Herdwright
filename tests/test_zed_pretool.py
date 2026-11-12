import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _load

import zeddeny

pt = _load.load("zed-pretool")
ZED, PROFILE = Path("/zed-home"), Path("/zed-home/profile")
ROOTS = [ZED, PROFILE, Path("/tmp"), Path("/private/tmp")]
COORD = "sess-1"


def decide(tool, cwd="/zed-home", holder=COORD, sid=COORD, **inp):
    return pt.decide({"session_id": sid, "tool_name": tool, "tool_input": inp, "cwd": cwd}, holder=holder, roots=ROOTS)


def bash(cmd, **kw):
    return decide("Bash", command=cmd, **kw)


class PretoolTests(unittest.TestCase):
    def test_only_the_lock_holder_is_constrained(self):
        self.assertIsNone(decide("Write", sid="worker", holder=COORD, file_path="/code/proj/a.py"))
        self.assertIsNone(decide("Write", holder=None, file_path="/code/proj/a.py"))
        self.assertIsNotNone(decide("Write", file_path="/code/proj/a.py"))

    def test_edits(self):
        self.assertIsNone(decide("Edit", file_path="/zed-home/bin/zed"))
        self.assertIsNone(decide("Write", file_path="/zed-home/profile/briefs/T1.md"))
        self.assertIsNone(decide("Write", file_path="/tmp/scratch.txt"))
        self.assertIsNone(decide("Write", file_path="notes.md", cwd="/zed-home"))
        self.assertIn("spawn a worker", decide("Edit", file_path="/code/proj/a.py"))
        self.assertIsNotNone(decide("NotebookEdit", notebook_path="/code/proj/n.ipynb"))
        self.assertIsNotNone(decide("Edit", file_path="/zed-home/../code/x.py"))

    def test_reads_are_never_blocked(self):
        for tool in ("Read", "Grep", "Glob", "WebFetch"):
            self.assertIsNone(decide(tool, file_path="/code/proj/a.py", pattern="x"))
        for cmd in ("ls /code/proj", "git -C /code/proj log --oneline", "git -C /code/proj status", "cat /code/proj/a.py | head",
                    "grep -rn foo /code/proj", "gh pr view 1", "bin/zed status"):
            self.assertIsNone(bash(cmd), cmd)

    def test_git_writes(self):
        self.assertIsNone(bash("git commit -m x"))                       # cwd is Zed's repo
        self.assertIsNone(bash("git add -A && git commit -qm 'ZED | x'"))
        self.assertIsNone(bash("git -C /zed-home commit -m x", cwd="/code/proj"))
        self.assertIn("git commit", bash("git commit -m x", cwd="/code/proj"))
        self.assertIn("git push", bash("git -C /code/proj push origin main"))
        self.assertIsNotNone(bash("git -c user.name=x -C /code/proj reset --hard"))
        self.assertIsNotNone(bash("echo hi && git rebase main", cwd="/code/proj"))

    def test_cd(self):
        self.assertIsNone(bash("cd /zed-home/bin && ls"))
        self.assertIsNone(bash("cd /tmp && ls"))
        self.assertIsNone(bash("cd -"))
        self.assertIn("don't `cd`", bash("cd /code/proj && git status"))
        self.assertIsNone(bash("(cd /code/proj && git status)"))  # subshell: does not persist

    def test_file_writers(self):
        self.assertIsNone(bash("echo x > /tmp/a"))
        self.assertIsNone(bash("echo x >> profile/notes.md"))
        self.assertIsNone(bash("echo x 2>&1 | cat"))
        self.assertIsNotNone(bash("echo x > /code/proj/a.txt"))
        self.assertIsNotNone(bash("rm -rf /code/proj/node_modules"))
        self.assertIsNotNone(bash("sed -i '' s/a/b/ /code/proj/a.py"))
        self.assertIsNone(bash("rm -f /tmp/x"))
        self.assertIsNotNone(bash("cp ~/notes.txt ~/.ssh/x"))

    def test_a_relative_write_is_resolved_where_the_shell_stands(self):
        """S339 B1: `touch c.txt` with OpenCode's `workdir` in a project repo passed; so did `touch ../../x` on Claude Code."""
        for command in ("touch c.txt", "rm keep.txt", "rm -rf src", "sed -i '' s/a/b/ c.txt", "mkdir -p a/b", "cp c.txt d.txt",
                        "mv c.txt d.txt", "echo x > c.txt", "echo x | tee c.txt", "perl -pi -e s/a/b/ c.txt", "ln -s a b"):
            for where in ({"workdir": "/code/proj"}, {"workdir": "../code/proj"}, {"cwd": "/code/proj"}):  # OpenCode, OpenCode, Claude Code
                self.assertIn("outside Zed's repo/profile/tmp", decide("shell", command=command, **where), (command, where))
            self.assertIsNone(decide("shell", command=command, workdir="profile/briefs"), command)  # in Zed's own repo
            self.assertIsNone(bash(command), command)
        self.assertIn("`touch` on c.txt in /code/proj", decide("shell", command="touch c.txt", workdir="/code/proj"))
        for command in ("touch ../../outside.txt", "rm ../code/proj/a.py", "echo x >> ../x.txt", "touch bin/../../x"):
            self.assertIsNotNone(bash(command), command)
        self.assertIsNone(bash("touch ../zed-home/notes.md"))
        self.assertIsNone(decide("shell", command="ls && cat c.txt", workdir="/code/proj"))  # reads stay free

    def test_a_cd_before_a_write_is_followed(self):
        for command in ("(cd /code/proj && touch c.txt)", "(cd /code/proj; rm -rf src)", "(cd /code && cd proj && echo x > c.txt)",
                        "(cd ../code/proj && sed -i '' s/a/b/ c.txt)", "(cd /code/proj && git commit -m x)", "(pushd /code/proj && touch c.txt)",
                        "cd /tmp && cd ../code/proj && touch c.txt", "ls | (cd /code/proj && tee c.txt)"):
            self.assertIsNotNone(bash(command), command)
        for command in ("(cd /code/proj && ls) && touch c.txt", "(cd /code/proj); touch c.txt", "cd /code/proj | touch c.txt",
                        "cd /tmp && touch c.txt", "(cd /code/proj && git status); git commit -m x", "cd bin && touch ../notes.md"):
            self.assertIsNone(bash(command), command)
        # from a project repo (OpenCode's workdir), a cd into Zed's home makes the write Zed's own; one in a subshell does not
        self.assertIsNone(decide("shell", command="cd /zed-home && touch c.txt", workdir="/code/proj"))
        self.assertIsNotNone(decide("shell", command="(cd /zed-home) && touch c.txt", workdir="/code/proj"))
        # a directory the shell expands cannot be followed: absolute targets are still checked, relative ones are not
        self.assertIsNotNone(bash("cd $D && touch /code/proj/c.txt"))
        self.assertIsNone(bash("cd $D && touch c.txt"))
        self.assertIsNone(bash("cd - && git commit -m x"))

    def test_an_opener_or_a_wrapper_before_a_write_does_not_hide_it(self):
        """S341 and S342: only the first word of a simple command was read as the program."""
        for command in ("{ touch c.txt; }", "if true; then touch c.txt; fi", "for f in a b; do rm c.txt; done", "command touch c.txt",
                        "env touch c.txt", "env FOO=1 rm c.txt", "! rm keep.txt", "sudo rm keep.txt", "time rm keep.txt", "xargs rm keep.txt",
                        "nice -n 5 touch c.txt", "if true; then git commit -m x; fi",
                        # S351: `timeout`, a run of operators, a `find` with options before its paths
                        "timeout 5 rm c.txt", "timeout -k 1 5s rm c.txt", "stdbuf -oL touch c.txt", "true;>c.txt", "find -L . -name x -delete"):
            self.assertIsNotNone(decide("shell", command=command, workdir="/code/proj"), command)
            self.assertIsNone(bash(command), command)  # in Zed's own repo
        for command in ("{ cd /code/proj && touch c.txt; }", "if true; then cd /code/proj; touch c.txt; fi", "builtin cd /code/proj && touch c.txt",
                        "cd -P /code/proj", "pushd /code/proj", "cd"):
            self.assertIn("it silently changes where later commands run", bash(command), command)
        self.assertIsNone(decide("shell", command="if true; then ls; fi", workdir="/code/proj"))

    def test_every_redirect_and_pipe_operator_is_read(self):
        for command in ("echo x &> c.txt", "echo x &>> c.txt", "echo x >& c.txt", "ls |& tee c.txt"):
            self.assertIn("outside Zed's repo/profile/tmp", decide("shell", command=command, workdir="/code/proj"), command)
            self.assertIsNone(bash(command), command)
        for command in ("echo x >&2", "ls 2>&1 | cat", "echo x &> /tmp/out", "echo x >&-"):
            self.assertIsNone(decide("shell", command=command, workdir="/code/proj"), command)

    def test_writes_that_are_not_a_file_writers_first_word(self):
        """S342: a heredoc with the redirect after it, git clean and checkout, chmod, dd and a deleting find passed."""
        for command in ("cat <<EOF > c.txt\nx\nEOF", "git clean -fd", "git checkout -- .", "git switch main", "chmod +x c.txt",
                        "chown bob c.txt", "dd if=/dev/zero of=c.txt", "find . -delete", "find src -exec rm {} \\;"):
            self.assertIsNotNone(decide("shell", command=command, workdir="/code/proj"), command)
            self.assertIsNone(bash(command), command)
        self.assertIsNotNone(bash("cat <<EOF > /code/proj/c.txt\nx\nEOF"))
        for command in ("find . -name '*.py'", "find . -exec grep -l x {} \\;", "chmod +x /tmp/run.sh", "dd if=c.txt of=/tmp/c", "git log"):
            self.assertIsNone(decide("shell", command=command, workdir="/code/proj"), command)

    def test_an_edit_that_names_no_file_is_denied_to_the_lock_holder(self):
        """S338: zed-deny denies it; here an unknown input shape (a patch without file markers) passed silently."""
        for tool, inp in (("patch", {"patchText": "*** Begin Patch\n+x\n*** End Patch"}), ("patch", {"diff": "--- a\n+++ b"}),
                          ("write", {"filePath": "/code/proj/a.py", "content": "x"}), ("Edit", {})):
            self.assertIn("names no file", decide(tool, **inp), (tool, inp))
            self.assertIsNone(decide(tool, sid="ses_worker", **inp))  # not the lock holder

    def test_heredoc_body_and_bad_quotes_do_not_trip(self):
        self.assertIsNone(bash("cat > /tmp/a.md <<'EOF'\ncd /code/proj\ngit push\nEOF\nls"))
        self.assertIsNone(bash("echo 'unbalanced"))

    def test_subagents(self):
        self.assertIn("bin/zed spawn", decide("Agent", prompt="x"))
        self.assertIsNotNone(decide("Workflow"))
        self.assertIsNone(pt.decide({"session_id": COORD, "tool_name": "Agent", "tool_input": {}}, holder=COORD, roots=ROOTS, allow_subagents=True))

    def test_opencode_tool_names_and_inputs_are_checked_like_claudes(self):
        self.assertIn("spawn a worker", decide("edit", path="/code/proj/a.py", oldString="a", newString="b"))
        self.assertIn("spawn a worker", decide("write", path="/code/proj/a.py", content="x"))
        self.assertIsNone(decide("write", path="/zed-home/profile/briefs/T1.md", content="x"))
        patch = "*** Begin Patch\n*** Update File: /zed-home/README.md\n*** Add File: /code/proj/new.py\n+x\n*** End Patch"
        self.assertIn("/code/proj/new.py is outside", decide("patch", patchText=patch))
        self.assertIsNone(decide("patch", patchText="*** Begin Patch\n*** Update File: /zed-home/README.md\n*** End Patch"))
        self.assertIn("git commit", decide("shell", command="git commit -m x", workdir="/code/proj"))
        self.assertIn("git commit", decide("shell", command="git commit -m x", workdir="../code/proj", cwd="/zed-home"))
        self.assertIsNone(decide("shell", command="git commit -m x"))  # in Zed's own repo
        self.assertIsNone(decide("shell", command="bin/zed status"))
        self.assertIn("bin/zed spawn", decide("subagent", description="x", prompt="y"))
        for tool in ("read", "grep", "glob", "skill"):
            self.assertIsNone(decide(tool, path="/code/proj/a.py", pattern="x", id="relay"))
        self.assertIsNone(decide("write", sid="ses_worker", path="/code/proj/a.py", content="x"))  # not the lock holder

    def test_the_canary_is_denied_in_every_session(self):
        for tool, sid, holder in (("shell", "ses_new", COORD), ("Bash", COORD, COORD), ("shell", "ses_new", None)):
            reason = decide(tool, sid=sid, holder=holder, command=" zed-pretool-canary 1f2e3d4c")
            self.assertIn("canary: `zed-pretool-canary 1f2e3d4c` is denied in every session", reason)
        self.assertIsNone(decide("shell", sid="ses_new", command="echo zed-pretool-canary"))
        self.assertIsNone(decide("read", sid="ses_new", path="zed-pretool-canary"))

    def test_hook_json_and_fail_open(self):
        with tempfile.TemporaryDirectory() as d:
            payload = json.dumps({"session_id": "s", "tool_name": "Write", "tool_input": {"file_path": "/code/x"}})
            # no lock held by "s" in the real profile: allowed, no output
            r = subprocess.run([str(_load.HOME / "bin" / "zed-pretool")], input=payload, capture_output=True, text=True, env={"ZED_PROFILE_DIR": d, "PATH": "/usr/bin:/bin"})
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            (Path(d) / ".cache").mkdir()
            (Path(d) / ".cache" / "zed.lock").write_text(json.dumps({"agent_session": "s"}))
            r = subprocess.run([str(_load.HOME / "bin" / "zed-pretool")], input=payload, capture_output=True, text=True, env={"ZED_PROFILE_DIR": d, "PATH": "/usr/bin:/bin"})
            out = json.loads(r.stdout)["hookSpecificOutput"]
            self.assertEqual((r.returncode, out["permissionDecision"]), (0, "deny"))
            self.assertIn("zed-pretool:", out["permissionDecisionReason"])
            bad = subprocess.run([str(_load.HOME / "bin" / "zed-pretool")], input="not json", capture_output=True, text=True)
            self.assertEqual((bad.returncode, bad.stdout), (0, ""))


@unittest.skipUnless(shutil.which("node"), "node is needed to run the OpenCode plugin")
class CoordinatorPluginTests(unittest.TestCase):
    """The coordinator's generated plugin, run for real under node with the hook object OpenCode v2.2.7 calls it with."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.profile = self.root / "profile"
        (self.profile / ".cache").mkdir(parents=True)
        (self.profile / ".cache" / "zed.lock").write_text(json.dumps({"agent_session": "ses_coord"}))
        stub = self.root / "cfg" / "node_modules" / "@opencode" / "plugin"  # OpenCode supplies this package itself
        stub.mkdir(parents=True)
        (stub / "package.json").write_text('{"name": "@opencode/plugin", "type": "module", "main": "index.js"}')
        (stub / "index.js").write_text("export const Plugin = { define: (definition) => definition };\n")
        (self.root / "cfg" / "package.json").write_text('{"type": "module"}')

    def before(self, tool, tool_input, session="ses_coord", hook=_load.HOME):
        plugin = zeddeny.coordinator_plugin(self.root / "cfg", hook, {"ZED_PROFILE_DIR": str(self.profile)})
        plugin = plugin / "plugins" / "zed-pretool" / "index.js"
        script = (f'import plugin from {json.dumps(plugin.as_uri())};\n'
                  f'const hooks = {{}};\n'
                  f'await plugin.setup({{ location: {{ directory: {json.dumps(str(_load.HOME))} }}, '
                  f'tool: {{ hook: async (name, callback) => {{ hooks[name] = callback; }} }} }});\n'
                  f'try {{ await hooks["execute.before"]({{ tool: {json.dumps(tool)}, sessionID: {json.dumps(session)}, agent: "build", '
                  f'input: {json.dumps(tool_input)} }}); console.log(plugin.id + " allowed"); }}\n'
                  f'catch (e) {{ console.log("blocked: " + e.message); }}\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith("ZED_")}
        r = subprocess.run(["node", "--input-type=module", "-e", script], env=env, capture_output=True, text=True)
        return r.stdout.strip() or r.stderr.strip()

    def test_the_lock_holder_is_denied_an_edit_in_a_project_repo_and_allowed_its_own_work(self):
        self.assertIn("blocked: zed-pretool: /code/proj/a.py is outside Zed's repo", self.before("write", {"path": "/code/proj/a.py", "content": "x"}))
        self.assertIn("blocked: zed-pretool: `git push`", self.before("shell", {"command": "git -C /code/proj push"}))
        self.assertEqual(self.before("shell", {"command": "bin/zed status"}), "zed.pretool allowed")
        self.assertEqual(self.before("write", {"path": str(self.profile / "briefs" / "T1.md"), "content": "x"}), "zed.pretool allowed")

    def test_another_session_passes_and_the_canary_is_denied_to_any(self):
        self.assertEqual(self.before("write", {"path": "/code/proj/a.py", "content": "x"}, session="ses_worker"), "zed.pretool allowed")
        self.assertIn("blocked: zed-pretool: canary: `zed-pretool-canary ab12`", self.before("shell", {"command": "zed-pretool-canary ab12"}, session="ses_new"))

    def test_a_guard_that_cannot_run_denies(self):
        self.assertIn("blocked: ", self.before("shell", {"command": "ls"}, hook=self.root / "no-such-zed-home"))


if __name__ == "__main__":
    unittest.main()
