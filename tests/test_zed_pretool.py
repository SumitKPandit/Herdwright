import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import _load

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

    def test_heredoc_body_and_bad_quotes_do_not_trip(self):
        self.assertIsNone(bash("cat > /tmp/a.md <<'EOF'\ncd /code/proj\ngit push\nEOF\nls"))
        self.assertIsNone(bash("echo 'unbalanced"))

    def test_subagents(self):
        self.assertIn("bin/zed spawn", decide("Agent", prompt="x"))
        self.assertIsNotNone(decide("Workflow"))
        self.assertIsNone(pt.decide({"session_id": COORD, "tool_name": "Agent", "tool_input": {}}, holder=COORD, roots=ROOTS, allow_subagents=True))

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


if __name__ == "__main__":
    unittest.main()
