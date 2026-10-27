import json
import os
import re
import sqlite3
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import _load
import zedlib

hy = _load.load("zed-hygiene")
SCHEMA = re.search(r'SCHEMA = """(.*?)"""', (_load.HOME / "bin" / "zed").read_text(), re.S).group(1)
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, env=ENV, check=True).stdout


class PureTests(unittest.TestCase):
    def test_parse_worktrees(self):
        out = hy.parse_worktrees("worktree /a\nHEAD x\nbranch refs/heads/main\n\nworktree /b\nHEAD y\ndetached\nprunable gitdir file missing\n")
        self.assertEqual(out, [{"path": "/a", "branch": "main"}, {"path": "/b", "prunable": True}])

    def test_gone_branches(self):
        self.assertEqual(hy.gone_branches("a [gone]\nb \nc [ahead 1]\nd [gone]\n"), ["a", "d"])


class RepoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        r = self.r = Path(self.tmp.name).resolve()
        git(r, "init", "-q", "--bare", "-b", "main", "origin.git")
        git(r, "clone", "-q", "origin.git", "clone")
        git(r / "clone", "commit", "-q", "--allow-empty", "-m", "init")
        git(r / "clone", "push", "-q", "origin", "main")
        self.root = r / "worktrees"
        self.root.mkdir()
        # a second clone pushes a new commit so the first is behind origin
        git(r, "clone", "-q", "origin.git", "other")
        git(r / "other", "commit", "-q", "--allow-empty", "-m", "second")
        git(r / "other", "push", "-q", "origin", "main")
        self.proj = {"path": str(r / "clone"), "dev_base": "main", "branches": {"main": "x"}}

    def run_project(self, **kw):
        return hy.project_findings("proj", self.proj, self.root, kw.get("owned", set()), kw.get("fetch", False), kw.get("apply", False))

    def test_orphan_owned_merged_gone_and_behind(self):
        owned_wt, orphan_wt = self.root / "owned", self.root / "orphan"
        git(self.r / "clone", "worktree", "add", "-q", "-b", "task/a", str(owned_wt), "origin/main")
        git(self.r / "clone", "worktree", "add", "-q", "-b", "task/b", str(orphan_wt), "origin/main")
        (orphan_wt / "x.txt").write_text("x")
        git(self.r / "clone", "branch", "old-merged", "main")
        git(self.r / "clone", "fetch", "-q")
        findings, _ = self.run_project(owned={str(owned_wt)})
        text = "\n".join(findings)
        self.assertIn(f"orphan-worktree {orphan_wt} (task/b; 1 uncommitted, 0 unpushed)", text)
        self.assertNotIn(str(owned_wt), text)
        self.assertIn("merged-branch old-merged", text)
        self.assertNotIn("merged-branch task/", text)  # checked out in a worktree
        self.assertIn("behind main by 1 commit", text)

    def test_apply_fast_forwards_and_prunes(self):
        wt = self.root / "gone"
        git(self.r / "clone", "worktree", "add", "-q", "-b", "task/c", str(wt), "origin/main")
        subprocess.run(["rm", "-rf", str(wt)], check=True)
        findings, actions = self.run_project(apply=True)
        self.assertTrue(any("fast-forward main ok" in a for a in actions), actions)
        self.assertTrue(any("worktree prune" in a for a in actions))
        self.assertEqual(git(self.r / "clone", "rev-list", "--count", "main").strip(), "2")
        self.assertEqual(git(self.r / "clone", "log", "-1", "--format=%s").strip(), "second")

    def test_dirty_clone_is_not_fast_forwarded(self):
        (self.r / "clone" / "README").write_text("x")
        git(self.r / "clone", "add", "README")
        git(self.r / "clone", "fetch", "-q")
        findings, actions = self.run_project(apply=True)
        self.assertIn("not fast-forwardable", "\n".join(findings))
        self.assertFalse(any("fast-forward" in a for a in actions))

    def test_gone_branch(self):
        git(self.r / "clone", "checkout", "-q", "-b", "task/d")
        git(self.r / "clone", "push", "-q", "-u", "origin", "task/d")
        git(self.r / "clone", "checkout", "-q", "main")
        git(self.r / "other", "push", "-q", "origin", ":task/d")
        findings, _ = self.run_project(fetch=True)
        self.assertIn("gone-branch task/d", "\n".join(findings))

    def test_cli_and_stale_panes(self):
        prof = self.r / "profile"
        (prof / "db").mkdir(parents=True)
        (prof / "config").mkdir()
        (prof / "config" / "projects.json").write_text(json.dumps({"worktree_root": str(self.root), "repos": {"proj": self.proj}}))
        conn = sqlite3.connect(prof / "db" / "zed.db")
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO sessions (task_id,repo,base,branch,worktree,agent,pane_id,status,created_at,updated_at) "
                     "VALUES (1,'proj','main','b','/x','a1','w1:p1','closed','x','x')")
        conn.commit()
        conn.row_factory = sqlite3.Row
        self.assertEqual(hy.pane_findings(conn, {}), [])
        self.assertIn("stale-pane w1:p1", hy.pane_findings(conn, {"w1:p1": {}})[0])
        out = subprocess.run([str(_load.HOME / "bin" / "zed-hygiene"), "report", "--fetch"], capture_output=True, text=True,
                             env={**os.environ, "ZED_PROFILE_DIR": str(prof)})
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("behind main by", out.stdout)


class ScratchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve() / "docs"
        self.folder.mkdir()
        self.projects = {"docs": {"path": str(self.folder), "worktrees": False}, "r": {"path": "/nowhere"}}
        patcher = unittest.mock.patch.object(zedlib, "projects", return_value=self.projects)
        patcher.start()
        self.addCleanup(patcher.stop)

    def conn(self, live_scratch):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO sessions (task_id,repo,base,branch,worktree,agent,scratch,status,created_at,updated_at) "
                     "VALUES (1,'docs','-','-',?,'live',?,'running','x','x')", (str(self.folder), str(live_scratch)))
        return conn

    def test_orphan_scratch_is_reported_and_only_removed_when_it_holds_no_unique_work(self):
        live, orphan, unique, other = (self.folder / n for n in ("T1-live", "T9-old", "T8-pr", "notes"))
        for d in (live, orphan, unique, other):
            d.mkdir()
        (orphan / "clone").mkdir()
        git(unique, "init", "-q", "-b", "main", "repo")
        git(unique / "repo", "commit", "-q", "--allow-empty", "-m", "mine")
        conn = self.conn(live)
        findings, actions = hy.scratch_findings(conn, apply=False)
        self.assertEqual(len(findings), 2)
        self.assertEqual(actions, [])
        self.assertTrue(orphan.exists())
        findings, actions = hy.scratch_findings(conn, apply=True)
        self.assertEqual(actions, [f"docs: removed scratch {orphan}"])
        self.assertFalse(orphan.exists())
        self.assertTrue(live.exists() and unique.exists() and other.exists())
        self.assertIn("kept: repo: commits not on any remote", "\n".join(findings))

    def test_a_db_without_the_scratch_column_is_tolerated(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE sessions (status TEXT)")
        conn.row_factory = sqlite3.Row
        self.assertEqual(hy.scratch_findings(conn, apply=False), ([], []))


if __name__ == "__main__":
    unittest.main()
