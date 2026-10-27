import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _load

xref = _load.load("zed-xref")


class XrefTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"}
        files = {"api": {"views.py": "def get_total(x):\n    return x\n", "other.py": "nothing here\n"},
                 "web": {"src/client.ts": "const t = get_total(1)\nconst u = get_total(2)\n"},
                 "docs": {"notes.md": "no symbol\n"}}
        self.repos = {}
        for name, tree in files.items():
            repo = root / name
            for rel, text in tree.items():
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                (repo / rel).write_text(text)
            subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "x"], check=True, env=env)
            self.repos[name] = {"path": str(repo)}
        self.repos["missing"] = {"path": str(root / "nowhere")}

    def run_main(self, *argv):
        out = io.StringIO()
        with patch.object(xref.zedlib, "projects", return_value=self.repos), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            code = xref.main(list(argv))
        return code, out.getvalue()

    def test_finds_a_symbol_in_two_repos_and_skips_the_rest(self):
        code, out = self.run_main("get_total")
        self.assertEqual(code, 0)
        self.assertIn("api: 1 match(es)", out)
        self.assertIn("web: 2 match(es)", out)
        self.assertIn("src/client.ts:1:", out)
        self.assertNotIn("docs:", out)
        self.assertNotIn("missing", out)

    def test_repo_filter_json_and_not_found(self):
        code, out = self.run_main("get_total", "--repo", "web", "--json")
        self.assertEqual(list(json.loads(out)), ["web"])
        self.assertEqual(self.run_main("get_total", "--repo", "nope")[0], 2)
        code, out = self.run_main("absent_symbol")
        self.assertEqual((code, out.strip()), (1, "not found in any repo"))

    def test_path_lists_files_named_so_and_max_caps_matches(self):
        code, out = self.run_main("src/client.ts", "--max", "1")
        self.assertEqual(code, 0)
        self.assertIn("file src/client.ts", out)
        code, out = self.run_main("get_total", "--max", "1")
        self.assertIn("... 1 more in web", out)

    def test_regex_mode(self):
        self.assertEqual(self.run_main("get_t.tal", "--fixed")[0], 1)
        code, out = self.run_main("get_t.tal", "--regex")
        self.assertEqual(code, 0)
        self.assertIn("api:", out)


if __name__ == "__main__":
    unittest.main()
