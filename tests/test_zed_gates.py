"""Lessons turned into code: the merge gate (verdicts attach to the code, not the commit), prompt guard, relay-post,
core-sync check. Each gate is tested for the refusal and the allowed case."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _load
import zedgates
import zedlib
from test_zed_assign import AssignCase

HOME = Path(__file__).resolve().parents[1]


class PureGateTests(unittest.TestCase):
    def test_the_gate_check_a_hook_runs_never_sees_the_override(self):
        with patch.dict(os.environ, {"ZED_GATE_OVERRIDE": "worker says so"}), patch.object(zedgates.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, "", "no review")
            self.assertEqual(zedgates.gate_reason(HOME), "no review")
        self.assertNotIn("ZED_GATE_OVERRIDE", run.call_args.kwargs["env"])

    def test_prompt_refusal(self):
        self.assertIn("working", zedgates.prompt_refusal("working", False))
        self.assertIsNone(zedgates.prompt_refusal("working", True))
        for state in ("idle", "done", "blocked"):
            self.assertIsNone(zedgates.prompt_refusal(state, False))

    def test_relay_prompt_quotes_the_words_and_the_action(self):
        text = zedgates.relay_prompt("  yes, approve it \n", "gh pr review 12 -R o/r --approve")
        self.assertIn("verbatim:\n    yes, approve it\n", text)
        self.assertIn("gh pr review 12 -R o/r --approve", text)
        with self.assertRaises(ValueError):
            zedgates.relay_prompt("", "x")
        with self.assertRaises(ValueError):
            zedgates.relay_prompt("ok", " ")

    def test_relay_prompt_keeps_quotes_and_lines_in_the_words_unambiguous(self):
        text = zedgates.relay_prompt('say "go"\nthen stop', "post it")
        self.assertIn('    say "go"\n    then stop\n', text)

class GateCase(AssignCase):
    """A branch with a commit, and session rows seeded for build/review/qa."""

    def setUp(self):
        super().setUp()
        (self.worktree / "x.txt").write_text("x\n")
        for cmd in (["config", "user.email", "t@t"], ["config", "user.name", "t"], ["add", "."], ["commit", "-qm", "work"]):
            zedlib.git(self.worktree, *cmd)
        self.sha = zedlib.git(self.worktree, "rev-parse", "HEAD").stdout.strip()
        self.tree = zedlib.core_tree(self.worktree)

    def row(self, hat, verdict=None, tree=None, core_start="same", status="finished"):
        """A session row. A reviewer that left the code alone began with the tree it gave its verdict about (`core_start="same"`)."""
        core_start = tree if core_start == "same" else core_start
        cur = self.conn.execute(
            "INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,status,hat,verdict,verdict_tree,core_start,created_at,updated_at) "
            "VALUES (20,'api','main',?,?,?,?,?,?,?,?,'2026-10-27 10:00','2026-10-27 10:00')",
            (self.branch, str(self.worktree), f"{hat}-t20", status, hat, verdict, tree, core_start))
        self.conn.commit()
        return cur.lastrowid

    def problems(self, tree=None):
        return zedgates.merge_problems(self.conn, tree or self.tree)


class MergeGateTests(GateCase):
    def test_refused_without_any_verdict(self):
        self.row("build")
        self.assertEqual(len(self.problems()), 2)

    def test_allowed_with_clean_review_and_qa(self):
        self.row("build")
        self.row("review", "clean", self.tree)
        self.row("qa", "clean", self.tree)
        self.assertEqual(self.problems(), [])

    def test_refused_when_one_hat_is_missing_or_has_blockers(self):
        self.row("build")
        self.row("review", "clean", self.tree)
        self.assertIn("no qa verdict", self.problems()[0])
        self.row("qa", "blockers", self.tree)
        self.assertIn("not clean", self.problems()[0])

    def test_a_later_blockers_verdict_overrides_an_earlier_clean_one(self):
        self.row("build")
        self.row("review", "clean", self.tree)
        self.row("qa", "clean", self.tree)
        self.assertEqual(self.problems(), [])
        self.row("review", "blockers", self.tree)  # a second reviewer, same code
        self.assertIn("review of", self.problems()[0])
        self.row("review", "clean", self.tree)  # and a later clean re-run clears it
        self.assertEqual(self.problems(), [])

    def test_refused_when_the_verdict_was_for_other_code(self):
        self.row("build")
        self.row("review", "clean", "0" * 40)
        self.row("qa", "clean", "0" * 40)
        self.assertEqual(len(self.problems()), 2)

    def test_a_verdict_row_of_an_author_hat_never_counts(self):
        """The author can never review or QA their own task: only a review or qa session's verdict is evidence."""
        for hat in ("build", None):
            self.conn.execute("DELETE FROM sessions")
            self.row(hat, "clean", self.tree)  # forced into the DB: a build/no-hat session carrying a "review" verdict
            self.row("qa", "clean", self.tree)
            self.conn.execute("UPDATE sessions SET hat=? WHERE id=1", (hat,))
            self.assertEqual(len(self.problems()), 1, hat)
            self.assertIn("no review verdict", self.problems()[0])

    def test_a_review_that_changed_the_code_is_not_independent(self):
        self.row("build")
        self.row("review", "clean", self.tree, core_start="1" * 40)  # it began with other code than it judged
        self.row("qa", "clean", self.tree)
        self.assertIn("review of", self.problems()[0])

    def test_gate_check_command_exit_codes(self):
        self.row("build")
        with self.assertRaises(SystemExit) as e:
            self.z.cmd_gate(["check", self.branch, str(self.repo)])
        self.assertEqual(e.exception.code, 1)
        self.assertIn("merge gate: no review verdict", self.output.getvalue())
        self.row("review", "clean", self.tree)
        self.row("qa", "clean", self.tree)
        with self.assertRaises(SystemExit) as e:
            self.z.cmd_gate(["check", self.branch, str(self.repo)])
        self.assertEqual(e.exception.code, 0)

    def test_merge_command_refuses_then_merges_past_the_gate(self):
        self.row("build")
        with self.assertRaises(SystemExit):
            self.z.cmd_merge([self.branch, str(self.repo)])
        self.assertIn("refused", self.output.getvalue())
        self.assertFalse((self.repo / "x.txt").exists())
        self.row("review", "clean", self.tree)
        self.row("qa", "clean", self.tree)
        with self.assertRaises(SystemExit) as e:
            self.z.cmd_merge([self.branch, str(self.repo)])
        self.assertEqual(e.exception.code, 0)
        self.assertTrue((self.repo / "x.txt").exists())
        self.assertIn("gate passed", self.conn.execute("SELECT text FROM events WHERE kind='merge'").fetchone()[0])

    def test_merge_refuses_a_conflicting_branch_and_leaves_the_checkout_untouched(self):
        self.row("build")
        self.row("review", "clean", self.tree)
        self.row("qa", "clean", self.tree)
        (self.repo / "x.txt").write_text("other\n")
        for cmd in (["add", "."], ["commit", "-qm", "conflicting"]):
            zedlib.git(self.repo, *cmd)
        head = zedlib.git(self.repo, "rev-parse", "HEAD").stdout.strip()
        with self.assertRaises(SystemExit) as e:
            self.z.cmd_merge([self.branch, str(self.repo), "--override", "even overridden"])
        self.assertNotEqual(e.exception.code, 0)
        self.assertEqual(zedlib.git(self.repo, "rev-parse", "HEAD").stdout.strip(), head)
        self.assertEqual(zedlib.git(self.repo, "status", "--porcelain").stdout, "")
        self.assertFalse((self.repo / ".git" / "MERGE_HEAD").exists())
        self.assertIsNone(self.conn.execute("SELECT 1 FROM events WHERE kind='merge'").fetchone())

    def test_merge_override_is_logged_with_its_reason(self):
        self.row("build")
        with self.assertRaises(SystemExit) as e:
            self.z.cmd_merge([self.branch, str(self.repo), "--override", "user said so"])
        self.assertEqual(e.exception.code, 0)
        self.assertIn("gate overridden: user said so", self.conn.execute("SELECT text FROM events WHERE kind='merge'").fetchone()[0])

    def test_gate_check_env_override_is_logged(self):
        self.row("build")
        with patch.dict(os.environ, {"ZED_GATE_OVERRIDE": "user said so"}), self.assertRaises(SystemExit) as e:
            self.z.cmd_gate(["check", self.branch, str(self.repo)])
        self.assertEqual(e.exception.code, 0)
        self.assertIn("user said so", self.conn.execute("SELECT text FROM events WHERE kind='merge'").fetchone()[0])


class CoreTreeTests(unittest.TestCase):
    """A verdict is about the code outside profile/: a profile-only commit keeps it, any other change replaces it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.write("bin/zed", "core\n")
        self.write("profile.template/profile.md", "template\n")
        self.write("profile/profile.md", "mine\n")
        self.commit("init")

    def git(self, *a):
        return subprocess.run(["git", "-C", str(self.repo), *a], capture_output=True, text=True, check=True).stdout.strip()

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, msg):
        self.git("add", ".")
        self.git("commit", "-qm", msg)

    def tree(self, rev="HEAD"):
        return zedlib.core_tree(self.repo, rev)

    def test_a_profile_only_commit_keeps_the_hash_and_a_core_change_replaces_it(self):
        before = self.tree()
        self.write("profile/lessons.md", "learned\n")
        self.write("profile/briefs/T1.md", "brief\n")
        self.commit("profile only")
        self.assertEqual(self.tree(), before)
        self.write("bin/zed", "core changed\n")
        self.commit("core change")
        self.assertNotEqual(self.tree(), before)

    def test_a_template_change_or_a_new_core_file_replaces_it(self):
        before = self.tree()
        self.write("profile.template/profile.md", "template v2\n")
        self.commit("template")
        second = self.tree()
        self.assertNotEqual(second, before)
        self.write("README.md", "new\n")
        self.commit("readme")
        self.assertNotEqual(self.tree(), second)

    def test_it_is_deterministic_and_the_same_for_the_same_code_on_two_branches(self):
        self.git("checkout", "-q", "-b", "other")
        self.write("profile/x.md", "other branch profile\n")
        self.commit("profile on other")
        self.assertEqual(self.tree("other"), self.tree("main"))
        self.assertEqual(self.tree("other"), self.tree("other"))

    def test_a_repo_that_is_not_zed_keeps_its_profile_directory_in_the_hash(self):
        other = self.repo / "other"
        other.mkdir()
        subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
        (other / "profile").mkdir()
        (other / "profile" / "page.py").write_text("one\n")
        subprocess.run(["git", "-C", str(other), "add", "."], check=True)
        subprocess.run(["git", "-C", str(other), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "a"], check=True)
        first = zedlib.core_tree(other)
        (other / "profile" / "page.py").write_text("two\n")
        subprocess.run(["git", "-C", str(other), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "b"], check=True)
        self.assertNotEqual(zedlib.core_tree(other), first)  # no profile.template here: nothing is ignored
        self.assertEqual(zedlib.core_tree(other), subprocess.run(["git", "-C", str(other), "rev-parse", "HEAD^{tree}"], capture_output=True,
                                                                  text=True).stdout.strip())

    def test_outside_git_there_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(zedlib.core_tree(tmp))


class ZedCheckoutMergeTests(GateCase):
    """`bin/zed merge` in a Zed-shaped checkout: verdicts given at one head survive a profile-only commit after them and
    not a core change (T74: a memory-only merge commit used to force two extra review and QA rounds)."""

    def setUp(self):
        super().setUp()
        self.repo = self.root / "zedcore"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.write("bin/zed", "core\n")
        self.write("profile.template/profile.md", "template\n")
        self.write("profile/profile.md", "mine\n")
        self.commit("init")
        self.git("checkout", "-q", "-b", self.branch)
        self.write("bin/new.py", "feature\n")
        self.commit("feature")
        self.tree = zedlib.core_tree(self.repo)
        self.row("build")
        self.row("review", "clean", self.tree)
        self.row("qa", "clean", self.tree)
        self.git("checkout", "-q", "main")

    def git(self, *a):
        return subprocess.run(["git", "-C", str(self.repo), *a], capture_output=True, text=True, check=True).stdout.strip()

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, msg):
        self.git("add", ".")
        self.git("commit", "-qm", msg)

    def on_branch(self, name, text, msg):
        self.git("checkout", "-q", self.branch)
        self.write(name, text)
        self.commit(msg)
        self.git("checkout", "-q", "main")

    def merge(self):
        self.output.truncate(0)
        with self.assertRaises(SystemExit) as e:
            self.z.cmd_merge([self.branch, str(self.repo)])
        return e.exception.code

    def test_the_verdicts_pass_at_the_head_they_were_given_at(self):
        self.assertEqual(self.merge(), 0, self.output.getvalue())

    def test_a_profile_only_commit_after_clean_verdicts_keeps_the_merge_passing(self):
        self.on_branch("profile/briefs/T1-findings.md", "later notes\n", "profile: notes after the verdicts")
        self.assertEqual(self.problems(zedlib.core_tree(self.repo, self.branch)), [])
        self.assertEqual(self.merge(), 0, self.output.getvalue())
        self.assertTrue((self.repo / "profile" / "briefs" / "T1-findings.md").exists())

    def test_a_core_change_after_the_verdicts_fails_the_merge(self):
        self.on_branch("bin/more.py", "changed after review\n", "core change after the verdicts")
        self.assertEqual(self.merge(), 1)
        self.assertIn("no review verdict recorded for this code", self.output.getvalue())
        self.assertFalse((self.repo / "bin" / "new.py").exists())

    def test_a_core_change_still_fails_when_a_profile_commit_follows_it(self):
        self.on_branch("bin/more.py", "changed after review\n", "core change")
        self.on_branch("profile/x.md", "more\n", "profile commit on top")
        self.assertEqual(self.merge(), 1)


class PushGateTests(GateCase):
    def test_enabled_guard_pushes_accept_independent_evidence_and_reject_invalid_evidence(self):
        env = {**os.environ, "ZED_PROFILE_DIR": str(self.profile)}
        bare = self.root / "remote.git"
        self.assertTrue(zedlib.git(self.root, "init", "-q", "--bare", str(bare)).ok)
        install = subprocess.run([str(HOME / "bin/zed-guard"), "install", str(self.worktree),
                                  "--base", "main", "--gate", "--ticket", "ABC-20"],
                                 capture_output=True, text=True, env=env)
        self.assertEqual(install.returncode, 0, install.stderr)
        config = Path(zedlib.git(self.worktree, "rev-parse", "--absolute-git-dir").stdout.strip()) / "zed-hooks/config.json"
        scenarios = ("clean", "no-verdict", "stale", "blockers", "author-hat", "changed-code")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                self.conn.execute("DELETE FROM sessions")
                self.conn.commit()
                self.row("build")
                if scenario == "no-verdict":
                    pass
                elif scenario == "author-hat":
                    review = self.row("review", "clean", self.tree)
                    self.row("qa", "clean", self.tree)
                    self.conn.execute("UPDATE sessions SET hat='build' WHERE id=?", (review,))
                    self.conn.commit()
                else:
                    tree = "0" * 40 if scenario == "stale" else self.tree
                    self.row("review", "blockers" if scenario == "blockers" else "clean", tree,
                             core_start="1" * 40 if scenario == "changed-code" else "same")
                    self.row("qa", "clean", tree)
                allowed = scenario == "clean"
                self.assertEqual(self.problems() == [], allowed)
                gate = subprocess.run([str(HOME / "bin/zed"), "gate", "check", self.sha, str(self.worktree)],
                                      capture_output=True, text=True, env=env)
                self.assertEqual(gate.returncode, 0 if allowed else 1, gate.stderr)
                before = config.read_bytes()
                self.assertTrue(json.loads(before)["gate"])
                ref = f"refs/heads/{scenario}"
                push = subprocess.run(["git", "-C", str(self.worktree), "push", str(bare), f"HEAD:{ref}"],
                                      capture_output=True, text=True, env=env)
                self.assertEqual(push.returncode, 0 if allowed else 1, push.stderr)
                readback = subprocess.run(["git", f"--git-dir={bare}", "show-ref", "--hash", "--verify", ref],
                                          capture_output=True, text=True, env=env)
                self.assertEqual(readback.returncode, 0 if allowed else 128, readback.stderr)
                self.assertEqual(readback.stdout.strip(), self.sha if allowed else "")
                self.assertEqual(config.read_bytes(), before)


class MergeHookTests(unittest.TestCase):
    """hooks/reference-transaction in a real throwaway repo; `bin/zed gate check` is a stub that exits with $GATE."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()
        (self.repo / "bin").mkdir()
        (self.repo / "bin" / "zed").write_text('#!/bin/sh\n[ "$1 $2" = "gate check" ] && exit "${GATE:-1}"\nexit 0\n')
        (self.repo / "bin" / "zed").chmod(0o755)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.git("config", "core.hooksPath", str(Path(__file__).resolve().parents[1] / "hooks"))
        self.commit("a.txt", "init")
        self.git("checkout", "-q", "-b", "profile/me")
        self.git("checkout", "-q", "-b", "feature/x", "main")
        self.commit("b.txt", "feature")
        self.git("checkout", "-q", "profile/me")
        self.commit("c.txt", "profile work")  # diverged, so merges make a merge commit

    def git(self, *a, env=None):
        return subprocess.run(["git", "-C", str(self.repo), *a], capture_output=True, text=True,
                              env={**os.environ, **(env or {})})

    def commit(self, name, msg):
        (self.repo / name).write_text(msg)
        self.git("add", ".")
        self.git("commit", "-qm", msg)

    def test_merge_into_a_profile_branch_is_refused_without_a_clean_gate(self):
        r = self.git("merge", "--no-ff", "-m", "m", "feature/x")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refused (needs clean review+qa", r.stderr)
        self.assertIn("git reset --hard HEAD", r.stderr)
        self.git("reset", "-q", "--hard", "HEAD")
        self.assertFalse((self.repo / "b.txt").exists())

    def test_merge_is_allowed_when_the_gate_passes(self):
        r = self.git("merge", "--no-ff", "-m", "m", "feature/x", env={"GATE": "0"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.repo / "b.txt").exists())

    def test_merging_main_into_the_profile_branch_is_not_gated(self):
        self.git("checkout", "-q", "main")
        self.commit("d.txt", "main work")
        self.git("checkout", "-q", "profile/me")
        self.assertEqual(self.git("merge", "--no-ff", "-m", "m", "main").returncode, 0)

    def test_merge_into_main_is_gated_and_other_branches_are_not(self):
        self.git("checkout", "-q", "main")
        self.commit("e.txt", "main moves")
        self.assertNotEqual(self.git("merge", "--no-ff", "-m", "m", "feature/x").returncode, 0)
        self.git("reset", "-q", "--hard", "main")
        self.git("checkout", "-q", "-b", "scratch", "profile/me")
        self.assertEqual(self.git("merge", "--no-ff", "-m", "m", "feature/x").returncode, 0)

    def test_merge_command_skips_only_the_head_it_vetted(self):
        sha = self.git("rev-parse", "feature/x").stdout.strip()
        r = self.git("merge", "--no-ff", "-m", "m", "feature/x", env={"ZED_MERGE_GATE_DONE": sha})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_blanket_skip_flag_no_longer_bypasses_the_gate(self):
        for value in ("1", "0" * 40):
            r = self.git("merge", "--no-ff", "-m", "m", "feature/x", env={"ZED_MERGE_GATE_DONE": value})
            self.assertNotEqual(r.returncode, 0, value)
            self.git("reset", "-q", "--hard", "HEAD")

    def test_pulling_ones_own_branch_is_not_gated(self):
        # origin holds an earlier commit of profile/me that the local branch has not got: a plain `git pull` merges it
        self.git("branch", "mine-earlier", "profile/me~1")
        self.git("checkout", "-q", "mine-earlier")
        self.commit("f.txt", "earlier work on another machine")
        self.git("update-ref", "refs/remotes/origin/profile/me", "HEAD")
        self.git("checkout", "-q", "profile/me")
        self.assertEqual(self.git("merge", "--no-ff", "-m", "m", "origin/profile/me").returncode, 0)
        self.assertTrue((self.repo / "f.txt").exists())

    def test_a_remote_ref_of_another_branch_does_not_unlock_the_merge(self):
        self.git("update-ref", "refs/remotes/origin/feature/x", "feature/x")
        self.assertNotEqual(self.git("merge", "--no-ff", "-m", "m", "feature/x").returncode, 0)


class MergeHookEndToEndTests(GateCase):
    """The real hook with the real `bin/zed gate check`, a seeded profile DB, verdict rows and the override event."""

    def setUp(self):
        super().setUp()
        self.repo = self.root / "core"
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("ZED_")}, "ZED_PROFILE_DIR": str(self.profile)}
        shutil.copytree(HOME / "bin", self.repo / "bin", ignore=shutil.ignore_patterns("__pycache__"))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.git("config", "core.hooksPath", str(HOME / "hooks"))
        self.commit("a.txt", "init")
        self.git("checkout", "-q", "-b", self.branch)
        self.commit("b.txt", "feature")
        self.sha = self.git("rev-parse", "HEAD").stdout.strip()
        self.tree = zedlib.core_tree(self.repo, self.sha)
        self.git("checkout", "-q", "-b", "profile/me", "main")
        self.commit("c.txt", "profile work")

    def git(self, *a, env=None):
        return subprocess.run(["git", "-C", str(self.repo), *a], capture_output=True, text=True, env=env or self.env)

    def commit(self, name, msg):
        (self.repo / name).write_text(msg)
        self.git("add", ".")
        self.git("commit", "-qm", msg)

    def merge(self, **extra):
        return self.git("merge", "--no-ff", "-m", "m", self.branch, env={**self.env, **extra})

    def events(self):
        return [r[0] for r in self.conn.execute("SELECT text FROM events WHERE kind='merge'")]

    def test_refused_then_allowed_by_verdicts_then_override_is_logged(self):
        self.row("build")
        r = self.merge()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no review verdict recorded", r.stderr)
        self.git("reset", "-q", "--hard", "HEAD")

        self.row("review", "clean", self.tree)
        self.row("qa", "blockers", self.tree)
        self.assertNotEqual(self.merge().returncode, 0)
        self.git("reset", "-q", "--hard", "HEAD")

        r = self.merge(ZED_GATE_OVERRIDE="user said so")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("overridden", r.stderr)
        self.assertIn("user said so", " ".join(self.events()))
        self.git("reset", "-q", "--hard", "HEAD~1")

        self.row("qa", "clean", self.tree)
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.repo / "b.txt").exists())



class PromptTests(GateCase):
    def setUp(self):
        super().setUp()
        self.sid = self.row("build", status="running")
        self.conn.execute("UPDATE sessions SET pane_id='p1', agent='build-t20' WHERE id=?", (self.sid,))
        self.conn.commit()

    def state(self, value):
        return patch.object(self.z, "live_agents", return_value={"p1": {"agent_status": value}})

    def test_working_session_is_refused_unless_redirect(self):
        with self.state("working"):
            with self.assertRaises(SystemExit):
                self.z.cmd_prompt([f"S{self.sid}", "hello"])
            self.mock_prompt_worker.assert_not_called()
            self.z.cmd_prompt([f"S{self.sid}", "--redirect", "stop and do X"])
        self.assertEqual(self.mock_prompt_worker.call_args.args, ("build-t20", "stop and do X"))

    def test_idle_session_is_prompted_and_logged(self):
        with self.state("idle"):
            self.z.cmd_prompt([str(self.sid), "answer", "from", "the", "user"])
        self.assertEqual(self.mock_prompt_worker.call_args.args[1], "answer from the user")
        self.assertTrue(self.conn.execute("SELECT 1 FROM events WHERE kind='prompt'").fetchone())

    def test_relay_post_sends_the_users_words_and_the_action(self):
        words = self.root / "ok.txt"
        words.write_text("Yes, approve PR 7\n")
        with self.state("idle"):
            self.z.cmd_relay_post([f"S{self.sid}", str(words), "--action", "approve PR 7 in o/r"])
        sent = self.mock_prompt_worker.call_args.args[1]
        self.assertIn("verbatim:\n    Yes, approve PR 7\n", sent)
        self.assertIn("approve PR 7 in o/r", sent)
        with self.state("working"):
            with self.assertRaises(SystemExit):
                self.z.cmd_relay_post([f"S{self.sid}", str(words), "--action", "approve PR 7 in o/r"])

    def test_relay_post_print_sends_nothing(self):
        words = self.root / "ok.txt"
        words.write_text("ok to post")
        self.z.cmd_relay_post([f"S{self.sid}", str(words), "--action", "comment", "--print"])
        self.mock_prompt_worker.assert_not_called()
        self.assertIn("ok to post", self.output.getvalue())


def run_script(name, env, *args):
    return subprocess.run(["python3", str(HOME / "bin" / name), *args], capture_output=True, text=True, env=env)


class CoreSyncCheckTests(unittest.TestCase):
    def test_check_lists_unsynced_core_commits_and_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "bin").mkdir()
            script = repo / "bin" / "zed-core-sync"
            script.write_text((HOME / "bin" / "zed-core-sync").read_text())
            git = lambda *a: subprocess.run(["git", "-C", tmp, *a], check=True, capture_output=True, text=True)
            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@t")
            git("config", "user.name", "t")
            (repo / "a").write_text("a")
            git("add", "a")
            git("commit", "-qm", "init")
            git("checkout", "-qb", "profile/me")
            clean = subprocess.run(["python3", str(script), "--check"], capture_output=True, text=True, cwd=tmp)
            self.assertEqual(clean.returncode, 0)
            (repo / "core.txt").write_text("c")
            git("add", "core.txt")
            git("commit", "-qm", "core change")
            (repo / "profile").mkdir()
            (repo / "profile" / "p.md").write_text("p")
            git("add", "profile")
            git("commit", "-qm", "profile only")
            r = subprocess.run(["python3", str(script), "--check", "--json"], capture_output=True, text=True, cwd=tmp)
            self.assertEqual(r.returncode, 1)
            data = json.loads(r.stdout)
            self.assertEqual(data["count"], 1)
            self.assertGreater(data["oldest"], 0)


    def test_check_exits_2_off_a_profile_branch_so_callers_can_tell_it_from_waiting_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            git = lambda *a: subprocess.run(["git", "-C", tmp, *a], check=True, capture_output=True, text=True)
            git("init", "-q", "-b", "main")
            (Path(tmp) / "bin").mkdir()
            script = Path(tmp) / "bin" / "zed-core-sync"  # a copy: the script checks the repo it lives in
            script.write_text((HOME / "bin" / "zed-core-sync").read_text())
            r = subprocess.run(["python3", str(script), "--check"], capture_output=True, text=True, cwd=tmp)
            self.assertEqual(r.returncode, 2)
            self.assertIn("profile branch", r.stderr)


class SessionRefTests(unittest.TestCase):
    def test_session_ref(self):
        z = _load.load("zed")
        self.assertEqual(z.session_ref("S61"), "61")
        self.assertEqual(z.session_ref("61"), "61")
        self.assertEqual(z.session_ref("Sam"), "Sam")  # an agent name that starts with S
        self.assertEqual(z.session_ref("quill-t25"), "quill-t25")


if __name__ == "__main__":
    unittest.main()
