"""The git hooks in hooks/ (installed by bin/zed-setup as core.hooksPath), run by git itself in a throwaway copy of a
Zed checkout: `main` holds core only, the always-loaded files have a word budget, and a merge into `main` or a profile
branch needs a clean review and QA.

The copy links to this checkout's hooks/ and bin/ and has a `main` with core files, a worker's branch `task/work` cut
from it, and a profile branch `profile/me` (checked out) with a profile in profile/. So git runs the real hooks, and
they call the real `bin/zed` on the copy's profile."""
import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

import _load  # noqa: F401 (puts bin/lib on the path)
import zedlib
from _e2e import clean_env

HOME = Path(__file__).resolve().parents[1]
PROFILE_BRANCH = "profile/me"


TEMPLATE = None  # the checkout every test copies, built once (setUpModule)


def setUpModule():
    global TEMPLATE
    tmp = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(tmp.cleanup)
    TEMPLATE = repo = Path(tmp.name).resolve() / "zed"
    env = clean_env(repo.parent / "home", ZED_PROFILE_DIR=str(repo / "profile"))

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
    repo.mkdir()
    for shared in ("hooks", "bin"):  # links, not copies: the hooks and the `bin/zed` they call are this checkout's own files
        (repo / shared).symlink_to(HOME / shared)
    shutil.copy(HOME / ".gitignore", repo / ".gitignore")
    (repo / "profile.template").mkdir()
    (repo / "profile.template" / "profile.md").write_text("# Profile template\n")
    (repo / "CLAUDE.md").write_text("# Operating manual\n")
    git("init", "-q", "-b", "main")
    git("config", "core.hooksPath", "hooks")  # what bin/zed-setup does
    git("add", "-A")
    git("commit", "-q", "-m", "Core")
    git("checkout", "-q", "-b", "task/work")  # a worker's branch, cut from main
    (repo / "feature.txt").write_text("work\n")
    git("add", "-A")
    git("commit", "-q", "-m", "Do the work")
    git("checkout", "-q", "-b", PROFILE_BRANCH, "main")
    (repo / "profile").mkdir()
    (repo / "profile" / "profile.md").write_text("# A profile\n")
    git("add", "-A")
    git("commit", "-q", "-m", "Profile")


class HookCase(unittest.TestCase):
    """Each test works in its own copy of the template checkout."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.root / "zed"
        self.env = clean_env(self.root / "home", ZED_PROFILE_DIR=str(self.repo / "profile"))  # where a checkout keeps it
        shutil.copytree(TEMPLATE, self.repo, symlinks=True)

    def git(self, *args, check=True, **env):
        r = subprocess.run(["git", *args], cwd=self.repo, env=self.env | env, capture_output=True, text=True)
        if check:
            self.assertEqual(r.returncode, 0, f"git {' '.join(args)}\n{r.stdout}{r.stderr}")
        return r

    def write(self, path, text):
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_text(text)

    def commit(self, message, check=True):
        self.git("add", "-A")
        return self.git("commit", "-q", "-m", message, check=check)

    def head(self, ref="HEAD"):
        return self.git("rev-parse", ref).stdout.strip()


class PreCommit(HookCase):
    def test_main_takes_core_but_no_profile(self):
        self.git("checkout", "-q", "main")
        before = self.head()
        self.write("profile/notes.md", "mine\n")
        r = self.commit("Profile on main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing to commit profile/ on main", r.stderr)
        self.assertIn("profile/notes.md", r.stderr)
        self.assertEqual(self.head(), before)

        self.git("reset", "-q")
        self.write("README.md", "core\n")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "Core only")
        self.assertNotEqual(self.head(), before)

    def test_a_profile_branch_takes_profile_and_core(self):
        self.write("profile/notes.md", "mine\n")
        self.write("README.md", "core\n")
        self.commit("Both, on the profile branch")
        self.assertEqual(self.git("show", "--name-only", "--format=").stdout.split(), ["README.md", "profile/notes.md"])

    def test_the_always_loaded_files_have_a_word_budget(self):
        self.write("profile/config/settings.json", json.dumps({"context_budget_words": 5}))
        self.commit("A budget of five words")  # touches no always-loaded file: not checked
        before = self.head()
        self.write("CLAUDE.md", "# Operating manual\n" + "word " * 20)
        r = self.commit("Grow the manual", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("OVER BUDGET", r.stderr)
        self.assertIn("always-loaded context is over its word budget", r.stderr)
        self.assertEqual(self.head(), before)

        self.write("profile/config/settings.json", json.dumps({"context_budget_words": 100000}))
        self.commit("Grow the manual, within the budget")
        self.assertNotEqual(self.head(), before)


class PreMergeCommit(HookCase):
    def test_a_profile_branch_cannot_be_merged_into_main(self):
        self.git("checkout", "-q", "main")
        before = self.head()
        r = self.git("merge", "--no-ff", "-m", "Merge the profile", PROFILE_BRANCH, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing to commit profile/ on main", r.stderr)
        self.assertEqual(self.head("main"), before)


class PrePush(HookCase):
    def setUp(self):
        super().setUp()
        self.git("init", "-q", "--bare", str(self.root / "origin.git"))
        self.git("remote", "add", "origin", str(self.root / "origin.git"))

    def pushed(self, branch):
        return self.git("ls-remote", "--heads", "origin", branch).stdout.split()[:1]

    def test_a_main_that_holds_a_profile_is_not_pushed(self):
        self.git("push", "-q", "origin", "main")  # core only: goes out
        self.assertEqual(self.pushed("main"), [self.head("main")])
        self.git("push", "-q", "origin", PROFILE_BRANCH)  # a profile branch may carry its profile
        self.assertEqual(self.pushed(PROFILE_BRANCH), [self.head(PROFILE_BRANCH)])

        self.git("checkout", "-q", "main")
        core = self.head()
        self.git("merge", "-q", "--ff-only", PROFILE_BRANCH)  # a fast-forward: no commit hook sees it
        r = self.git("push", "origin", "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing to push refs/heads/main to main: its tree contains profile/", r.stderr)
        self.assertEqual(self.pushed("main"), [core])


class ReferenceTransaction(HookCase):
    """The merge gate: a merge commit written to `main` or a profile branch."""

    def setUp(self):
        super().setUp()
        self.work = self.head("task/work")
        self.before = self.head()

    def merge(self, branch="task/work", **env):
        return self.git("merge", "--no-ff", "-m", f"Merge {branch}", branch, check=False, **env)

    def verdicts(self, **by_hat):
        """Record a finished session per hat with its verdict about the work's code, as `zed-employee finish` does."""
        subprocess.run([str(self.repo / "bin" / "zed"), "task", "list"], env=self.env, check=True, capture_output=True)  # creates the DB
        tree = zedlib.core_tree(self.repo, self.work)
        conn = sqlite3.connect(self.repo / "profile" / "db" / "zed.db")
        with conn:
            for hat, verdict in by_hat.items():
                conn.execute(
                    "INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,status,hat,verdict,verdict_tree,core_start,"
                    "created_at,updated_at) VALUES (1,'zed','main','task/work',?,?,'closed',?,?,?,?,'2026-11-11 10:00','2026-11-11 10:00')",
                    (str(self.repo), f"{hat}-t1", hat, verdict, tree, tree))
        conn.close()

    def assert_refused(self, r):
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"merge into {PROFILE_BRANCH} refused (needs clean review+qa by non-authors)", r.stderr)
        self.assertEqual(self.head(PROFILE_BRANCH), self.before)  # the branch did not move
        self.git("reset", "-q", "--hard", "HEAD")

    def assert_merged(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.git("rev-list", "--parents", "-n", "1", PROFILE_BRANCH).stdout.split()[1:], [self.before, self.work])

    def test_unreviewed_work_is_not_merged(self):
        r = self.merge()
        self.assertIn("no review verdict recorded for this code", r.stderr)
        self.assertIn("no qa verdict recorded for this code", r.stderr)
        self.assert_refused(r)

    def test_a_review_alone_or_a_qa_with_blockers_is_not_enough(self):
        self.verdicts(review="clean")
        self.assert_refused(self.merge())
        self.verdicts(qa="blockers")
        self.assert_refused(self.merge())

    def test_work_with_a_clean_review_and_qa_is_merged(self):
        self.verdicts(review="clean", qa="clean")
        self.assert_merged(self.merge())

    def test_work_that_changed_after_its_verdicts_is_not_merged(self):
        self.verdicts(review="clean", qa="clean")
        self.git("checkout", "-q", "task/work")
        self.write("feature.txt", "more work\n")
        self.commit("Change it after the review")
        self.git("checkout", "-q", PROFILE_BRANCH)
        self.assert_refused(self.merge())

    def test_the_users_override_is_logged(self):
        r = self.merge(ZED_GATE_OVERRIDE="the user said so")
        self.assert_merged(r)
        self.assertIn("overridden (the user said so); logged", r.stderr)
        conn = sqlite3.connect(self.repo / "profile" / "db" / "zed.db")
        self.addCleanup(conn.close)
        logged = [row[0] for row in conn.execute("SELECT text FROM events WHERE kind='merge'")]
        self.assertEqual(len(logged), 1)
        self.assertIn("the user said so", logged[0])

    def test_what_is_not_unreviewed_work_arriving_is_not_gated(self):
        self.assert_refused(self.merge(ZED_MERGE_GATE_DONE="0" * 40))  # `bin/zed merge` vetted another head: still checked
        self.assert_merged(self.merge(ZED_MERGE_GATE_DONE=self.work))  # the head it vetted: not checked again

        self.git("reset", "-q", "--hard", self.before)
        self.git("update-ref", f"refs/remotes/origin/{PROFILE_BRANCH}", self.work)  # a pull: the remote's copy of this branch holds it
        self.assert_merged(self.merge())

        self.git("checkout", "-q", "main")  # what main holds was gated when it got there
        self.write("README.md", "core\n")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "More core")
        self.work = self.head()
        self.git("checkout", "-q", PROFILE_BRANCH)
        self.before = self.head()
        self.assert_merged(self.merge("main"))

        self.git("checkout", "-q", "-b", "task/other", "main")  # only main and profile branches are gated
        self.assertEqual(self.merge("task/work").returncode, 0)

    def test_main_is_gated_too(self):
        self.git("checkout", "-q", "main")
        before = self.head()
        r = self.merge()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("merge into main refused", r.stderr)
        self.assertEqual(self.head("main"), before)


if __name__ == "__main__":
    unittest.main()
