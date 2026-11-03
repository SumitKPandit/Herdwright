import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import _load
import zedlib

crew = _load.load("zed-crew")
SCHEMA = re.search(r'SCHEMA = """(.*?)"""', (_load.HOME / "bin" / "zed").read_text(), re.S).group(1)


def sh(*cmd, cwd=None):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


class CrewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        sh("git", "init", "-q", "--bare", "-b", "main", str(root / "origin.git"))
        sh("git", "clone", "-q", str(root / "origin.git"), str(root / "clone"))
        sh("git", "commit", "-q", "--allow-empty", "-m", "init", cwd=root / "clone")
        sh("git", "push", "-q", "origin", "main", cwd=root / "clone")
        self.wt = root / "wt"
        sh("git", "worktree", "add", "-q", "-b", "feat/x", str(self.wt), "origin/main", cwd=root / "clone")
        (self.wt / "a.txt").write_text("a\n")
        sh("git", "add", "a.txt", cwd=self.wt)
        sh("git", "commit", "-q", "-m", "MENT-1 | Add a", cwd=self.wt)
        self.briefs = root / "briefs"
        self.briefs.mkdir()
        old = (zedlib.BRIEFS_DIR, zedlib.PROJECTS_PATH)
        zedlib.BRIEFS_DIR, zedlib.PROJECTS_PATH = self.briefs, root / "none.json"
        self.addCleanup(lambda: setattr(zedlib, "BRIEFS_DIR", old[0]) or setattr(zedlib, "PROJECTS_PATH", old[1]))
        self.root = root

    def session(self, **kw):
        s = {"id": 1, "task_id": 9, "agent": "t9-x", "repo": "r", "base": "main", "branch": "feat/x", "worktree": str(self.wt),
             "pane_id": "p1", "pr_url": None, "hat": None, "plan": None, "status": "running", "external": 0, "runtime": "claude",
             "created_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - 3600))}
        s.update(kw)
        return s

    LIVE = {"p1": {"agent_status": "working"}}

    def flags(self, **kw):
        return crew.facts(self.session(**kw), kw.pop("live", self.LIVE))["flags"]

    def test_healthy_with_plan(self):
        (self.briefs / "T9-r-plan.md").write_text("- [x] 1. a\n- [ ] 2. b\n")
        (self.briefs / "T9-review-plan.md").write_text("- [ ] 1. other session's plan\n")
        f = crew.facts(self.session(plan=str(self.briefs / "T9-r-plan.md")), self.LIVE)
        self.assertEqual(f["flags"], [])
        self.assertEqual((f["plan"]["done"], f["plan"]["total"]), (1, 2))  # its own plan, not every plan of the task
        self.assertEqual((f["git"]["branch"], f["git"]["dirty"], f["git"]["ahead_of_base"]), ("feat/x", 0, 1))
        self.assertEqual(f["git"]["unpushed"], 1)  # the worktree tracks origin/main, yet the commit is on no remote branch
        sh("git", "push", "-q", "origin", "feat/x", cwd=self.wt)
        self.assertEqual(crew.facts(self.session(plan=str(self.briefs / "T9-r-plan.md")), self.LIVE)["git"]["unpushed"], 0)
        self.assertIn("1 file changed", f["git"]["diffstat"])
        self.assertTrue(any("build plan 1/2" in ln for ln in crew.render_session(f)))

    def test_no_plan_and_pane_gone(self):
        self.assertTrue(any(x.startswith("no-plan") for x in self.flags()))
        self.assertIn("pane-gone", crew.facts(self.session(), {})["flags"])

    def test_wrong_and_protected_branch(self):
        sh("git", "checkout", "-q", "-b", "other", cwd=self.wt)
        self.assertTrue(any(x.startswith("wrong-branch") for x in self.flags()))
        sh("git", "checkout", "-q", "-b", "deploy", cwd=self.wt)
        flags = crew.facts(self.session(branch="deploy"), self.LIVE)["flags"]
        self.assertTrue(any(x.startswith("on-protected-branch") for x in flags))

    def test_finished_with_work_at_risk(self):
        (self.wt / "dirty.txt").write_text("x")
        flags = crew.facts(self.session(status="finished", pane_id=None), {})["flags"]
        self.assertTrue(any(x.startswith("finished-unpushed (1 uncommitted, 1 unpushed)") for x in flags), flags)

    def test_closed_session_is_reported_only_while_its_worktree_holds_unlanded_work(self):
        closed = lambda: crew.facts(self.session(status="closed", pane_id=None), {})["flags"]  # noqa: E731
        self.assertEqual(closed(), ["closed-with-unlanded-work (0 uncommitted, 1 unpushed, on no other branch)"])
        (self.wt / "dirty.txt").write_text("x")
        self.assertEqual(closed(), ["closed-with-unlanded-work (1 uncommitted, 1 unpushed, on no other branch)"])
        (self.wt / "dirty.txt").unlink()
        sh("git", "branch", "landed-here", cwd=self.wt)  # merged into another local branch: landed, though on no remote
        self.assertEqual(closed(), [])
        sh("git", "branch", "-D", "landed-here", cwd=self.wt)
        sh("git", "push", "-q", "origin", "feat/x", cwd=self.wt)
        self.assertEqual(closed(), [])  # pushed: landed

    def test_a_branch_cut_from_an_unpushed_base_counts_only_its_own_commits(self):
        commit = lambda name: (  # noqa: E731
            (self.wt / name).write_text("x"), sh("git", "add", ".", cwd=self.wt), sh("git", "commit", "-q", "-m", name, cwd=self.wt))
        sh("git", "checkout", "-q", "-b", "base-local", cwd=self.wt)  # a local base holding history that is on no remote
        commit("b1.txt"), commit("b2.txt")
        sh("git", "checkout", "-q", "-b", "feat/y", cwd=self.wt)
        commit("own.txt")
        self.assertEqual(crew.git_facts(self.wt, "base-local")["unpushed"], 1)  # its own commit, not the base's history
        self.assertEqual(crew.git_facts(self.wt, "main")["unpushed"], 4)  # against a base it was not cut from, all of it is unpushed

    def test_missing_worktree(self):
        self.assertIn("worktree-missing", crew.facts(self.session(worktree=str(self.root / "nope")), self.LIVE)["flags"])

    def test_external_and_non_git_sessions_have_no_git_flags(self):
        self.assertEqual(crew.facts(self.session(external=1), self.LIVE)["git"], None)
        self.assertEqual([f for f in crew.facts(self.session(base="-", branch="-"), self.LIVE)["flags"] if "worktree" in f], [])

    def test_pr_flags(self):
        f = {"pr": "u"}
        self.assertEqual(crew.pr_flags(f, lambda u: "merged"), ["pr-merged (close the session and task?)"])
        self.assertEqual(crew.pr_flags(f, lambda u: "green"), [])
        self.assertTrue(crew.pr_flags(f, lambda u: 1 / 0)[0].startswith("pr-lookup-failed"))

    def test_cli_against_a_temp_profile(self):
        prof = self.root / "profile"
        (prof / "db").mkdir(parents=True)
        conn = sqlite3.connect(prof / "db" / "zed.db")
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO tasks (id,title,status,created_at,updated_at) VALUES (9,'t','active','x','x')")
        conn.execute("INSERT INTO sessions (task_id,repo,base,branch,worktree,agent,pane_id,status,created_at,updated_at) "
                     "VALUES (9,'r','main','feat/x',?,'t9-x','zz:nope','running','2000-01-01 00:00','x')", (str(self.wt),))
        conn.commit()
        env = {**os.environ, "ZED_PROFILE_DIR": str(prof)}
        out = subprocess.run([str(_load.HOME / "bin" / "zed-crew"), "state", "--json"], env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        data = json.loads(out.stdout)
        self.assertEqual(data[0]["git"]["branch"], "feat/x")
        rec = subprocess.run([str(_load.HOME / "bin" / "zed-crew"), "reconcile"], env=env, capture_output=True, text=True)
        self.assertEqual(rec.returncode, 1)
        self.assertIn("pane-gone", rec.stdout)


if __name__ == "__main__":
    unittest.main()
