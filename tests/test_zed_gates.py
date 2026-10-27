"""Lessons turned into code (T25): merge gate, employees first, Claude first, prompt guard, relay-post, core-sync
check, `bb pr approve|comment`, code-first `finish`. Each gate is tested for the refusal and the allowed case."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _load
import zedassign
import zedgates
import zedlib
from test_zed_assign import AssignCase
from test_zed_employee import ProfileCase

HOME = Path(__file__).resolve().parents[1]


class PureGateTests(unittest.TestCase):
    def test_spawn_without_employee(self):
        self.assertIn("juno", zedgates.spawn_without_employee(["juno"], {}))
        self.assertIsNone(zedgates.spawn_without_employee(["juno"], {"employee": "juno"}))
        self.assertIsNone(zedgates.spawn_without_employee(["juno"], {"ephemeral": "no employee knows Figma"}))
        self.assertIn("juno", zedgates.spawn_without_employee(["juno"], {"ephemeral": "  "}))  # a blank reason is no reason
        self.assertIsNone(zedgates.spawn_without_employee([], {}))

    def test_copilot_before_claude(self):
        usable = {"installed": True, "enabled": True, "limited": None}
        self.assertIn("claude is usable", zedgates.copilot_before_claude("copilot", usable, {}))
        self.assertIsNone(zedgates.copilot_before_claude("copilot", usable, {"copilot_reason": "user asked"}))
        self.assertIsNone(zedgates.copilot_before_claude("copilot", {**usable, "limited": {"note": "cap"}}, {}))
        self.assertIsNone(zedgates.copilot_before_claude("copilot", {**usable, "enabled": False}, {}))
        self.assertIsNone(zedgates.copilot_before_claude("claude", usable, {}))

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

    def test_core_sync_stale(self):
        self.assertTrue(zedgates.core_sync_stale(1000, 1000 + 3601))
        self.assertFalse(zedgates.core_sync_stale(1000, 1000 + 3600))
        self.assertFalse(zedgates.core_sync_stale(None, 10 ** 9))


class GateCase(AssignCase):
    """A branch with a commit, and session rows seeded for build/review/qa."""

    def setUp(self):
        super().setUp()
        (self.worktree / "x.txt").write_text("x\n")
        for cmd in (["config", "user.email", "t@t"], ["config", "user.name", "t"], ["add", "."], ["commit", "-qm", "work"]):
            zedlib.git(self.worktree, *cmd)
        self.sha = zedlib.git(self.worktree, "rev-parse", "HEAD").stdout.strip()

    def row(self, hat, employee, verdict=None, sha=None, status="finished"):
        cur = self.conn.execute(
            "INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,status,employee,hat,verdict,verdict_sha,created_at,updated_at) "
            "VALUES (20,'api','main',?,?,?,?,?,?,?,?,'2026-10-27 10:00','2026-10-27 10:00')",
            (self.branch, str(self.worktree), f"{employee}-{hat}", status, employee, hat, verdict, sha))
        self.conn.commit()
        return cur.lastrowid

    def problems(self, sha=None):
        return zedgates.merge_problems(self.conn, sha or self.sha)


class MergeGateTests(GateCase):
    def test_refused_without_any_verdict(self):
        self.row("build", "juno")
        self.assertEqual(len(self.problems()), 2)

    def test_allowed_with_clean_review_and_qa_by_non_authors(self):
        self.row("build", "juno")
        self.row("review", "quill", "clean", self.sha)
        self.row("qa", "pip", "clean", self.sha)
        self.assertEqual(self.problems(), [])

    def test_refused_when_one_hat_is_missing_or_has_blockers(self):
        self.row("build", "juno")
        self.row("review", "quill", "clean", self.sha)
        self.assertIn("no qa verdict", self.problems()[0])
        self.row("qa", "pip", "blockers", self.sha)
        self.assertIn("not clean", self.problems()[0])

    def test_a_later_blockers_verdict_overrides_an_earlier_clean_one(self):
        self.row("build", "juno")
        self.row("review", "quill", "clean", self.sha)
        self.row("qa", "pip", "clean", self.sha)
        self.assertEqual(self.problems(), [])
        self.row("review", "pip", "blockers", self.sha)  # a second reviewer, same commit
        self.assertIn("review of", self.problems()[0])
        self.row("review", "quill", "clean", self.sha)  # and a later clean re-run clears it
        self.assertEqual(self.problems(), [])

    def test_refused_when_the_verdict_was_for_an_older_head(self):
        self.row("build", "juno")
        self.row("review", "quill", "clean", "0" * 40)
        self.row("qa", "pip", "clean", "0" * 40)
        self.assertEqual(len(self.problems()), 2)

    def test_refused_when_the_reviewer_authored_the_work(self):
        self.row("build", "juno")
        self.row("build", "quill")  # quill fixed it, so quill is an author too
        self.row("review", "quill", "clean", self.sha)
        self.row("qa", "pip", "clean", self.sha)
        self.assertIn("review of", self.problems()[0])

    def test_gate_check_command_exit_codes(self):
        self.row("build", "juno")
        with self.assertRaises(SystemExit) as e:
            self.z.cmd_gate(["check", self.branch, str(self.repo)])
        self.assertEqual(e.exception.code, 1)
        self.assertIn("merge gate: no review verdict", self.output.getvalue())
        self.row("review", "quill", "clean", self.sha)
        self.row("qa", "pip", "clean", self.sha)
        with self.assertRaises(SystemExit) as e:
            self.z.cmd_gate(["check", self.branch, str(self.repo)])
        self.assertEqual(e.exception.code, 0)

    def test_merge_command_refuses_then_merges_past_the_gate(self):
        self.row("build", "juno")
        with self.assertRaises(SystemExit):
            self.z.cmd_merge([self.branch, str(self.repo)])
        self.assertIn("refused", self.output.getvalue())
        self.assertFalse((self.repo / "x.txt").exists())
        self.row("review", "quill", "clean", self.sha)
        self.row("qa", "pip", "clean", self.sha)
        with self.assertRaises(SystemExit) as e:
            self.z.cmd_merge([self.branch, str(self.repo)])
        self.assertEqual(e.exception.code, 0)
        self.assertTrue((self.repo / "x.txt").exists())
        self.assertIn("gate passed", self.conn.execute("SELECT text FROM events WHERE kind='merge'").fetchone()[0])

    def test_merge_override_is_logged_with_its_reason(self):
        self.row("build", "juno")
        with self.assertRaises(SystemExit) as e:
            self.z.cmd_merge([self.branch, str(self.repo), "--override", "user said so"])
        self.assertEqual(e.exception.code, 0)
        self.assertIn("gate overridden: user said so", self.conn.execute("SELECT text FROM events WHERE kind='merge'").fetchone()[0])


    def test_gate_check_env_override_is_logged(self):
        self.row("build", "juno")
        with patch.dict(os.environ, {"ZED_GATE_OVERRIDE": "user said so"}), self.assertRaises(SystemExit) as e:
            self.z.cmd_gate(["check", self.branch, str(self.repo)])
        self.assertEqual(e.exception.code, 0)
        self.assertIn("user said so", self.conn.execute("SELECT text FROM events WHERE kind='merge'").fetchone()[0])


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
        self.row("build", "juno")
        r = self.merge()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no review verdict recorded", r.stderr)
        self.git("reset", "-q", "--hard", "HEAD")

        self.row("review", "quill", "clean", self.sha)
        self.row("qa", "pip", "blockers", self.sha)
        self.assertNotEqual(self.merge().returncode, 0)
        self.git("reset", "-q", "--hard", "HEAD")

        r = self.merge(ZED_GATE_OVERRIDE="user said so")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("overridden", r.stderr)
        self.assertIn("user said so", " ".join(self.events()))
        self.git("reset", "-q", "--hard", "HEAD~1")

        self.row("qa", "pip", "clean", self.sha)
        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.repo / "b.txt").exists())


class HandoffEmployeeTests(GateCase):
    def hand_off(self, *extra, restart=True):
        sid = self.row("build", None, status="running")
        self.conn.execute("UPDATE sessions SET employee=NULL, hat=NULL, workspace_id='w', runtime='claude' WHERE id=?", (sid,))
        self.conn.commit()
        (self.profile / "briefs").mkdir(exist_ok=True)
        with patch.object(self.z, "close_workspace"), patch.object(self.z, "record_credits"), \
                patch.object(self.z, "record_spend"), patch.object(self.z, "cmd_spawn", return_value=9) as spawn:
            self.z.cmd_handoff([f"S{sid}", *(["restart"] if restart else []), f"brief={self.brief}", *extra])
        return spawn.call_args.args[0]

    def test_a_session_without_an_employee_goes_to_a_free_one(self):
        args = self.hand_off()
        self.assertTrue(any(a.startswith("employee=") for a in args))
        self.assertIn("hat=build", args)
        self.assertFalse(any(a.startswith("ephemeral=") for a in args))

    def test_ephemeral_reason_is_passed_through_while_employees_are_free(self):
        self.assertIn("ephemeral=no fit", self.hand_off("ephemeral=no fit"))

    def test_no_free_employee_allows_an_ephemeral_handoff(self):
        with patch.object(zedassign, "free_employees", return_value=[]):
            args = self.hand_off()
        self.assertIn("ephemeral=no employee is free", args)

    CLAUDE_USABLE = {"installed": True, "enabled": True, "limited": None}

    def test_handoff_to_copilot_while_claude_is_usable_is_refused_before_closing_anything(self):
        with patch.object(self.z, "choose_runtime", return_value="copilot"), \
                patch.object(self.z, "runtime_report", return_value=self.CLAUDE_USABLE), self.assertRaises(SystemExit):
            self.hand_off(restart=False)
        self.assertIn("claude is usable", self.output.getvalue())
        self.assertEqual(self.conn.execute("SELECT status FROM sessions").fetchone()[0], "running")

    def test_handoff_to_copilot_when_claude_is_limited_gives_no_reason_of_its_own(self):
        with patch.object(self.z, "choose_runtime", return_value="copilot"), \
                patch.object(self.z, "runtime_report", return_value={**self.CLAUDE_USABLE, "limited": {"note": "cap"}}):
            args = self.hand_off(restart=False)
        self.assertFalse(any(a.startswith("copilot_reason=") for a in args))

    def test_handoff_passes_the_callers_copilot_reason(self):
        with patch.object(self.z, "choose_runtime", return_value="copilot"), \
                patch.object(self.z, "runtime_report", return_value=self.CLAUDE_USABLE):
            args = self.hand_off("copilot_reason=user asked for it", restart=False)
        self.assertIn("copilot_reason=user asked for it", args)

    def test_nothing_eligible_refuses_before_closing_anything(self):
        with patch.object(zedassign, "suggest", return_value=[]), self.assertRaises(SystemExit):
            self.hand_off()
        self.assertIn("none is eligible", self.output.getvalue())


class VerdictShaTests(GateCase):
    def test_head_sha_is_the_worktree_head_and_none_outside_git(self):
        import zedemployee as emp
        self.assertEqual(emp.head_sha(self.worktree), self.sha)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(emp.head_sha(tmp))

    def test_core_sync_event_is_raised_once_per_oldest_commit(self):
        alerts = {}
        stale = subprocess.CompletedProcess([], 1, json.dumps({"count": 2, "oldest": 1000}), "")
        with patch.object(self.z, "run", return_value=stale):
            self.assertIn("2 core commit(s)", self.z.core_sync_events(alerts)[0])
            alerts["core_sync"]["checked"] = 0
            self.assertEqual(self.z.core_sync_events(alerts), [])  # same oldest commit: not again
        fresh = subprocess.CompletedProcess([], 1, json.dumps({"count": 1, "oldest": 2 * 10 ** 10}), "")
        with patch.object(self.z, "run", return_value=fresh):
            alerts["core_sync"]["checked"] = 0
            self.assertEqual(self.z.core_sync_events(alerts), [])  # younger than an hour
        with patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 1, "", "not a profile branch")):
            alerts["core_sync"]["checked"] = 0
            self.assertEqual(self.z.core_sync_events(alerts), [])


class SpawnGateTests(GateCase):
    def plain(self, **extra):
        return self.z.cmd_spawn(["20", "repo=api", "base=main", f"branch={self.branch}", f"brief={self.brief}",
                                 *[f"{k}={v}" for k, v in extra.items()]])

    def test_spawn_without_employee_is_refused_while_one_is_free(self):
        with self.assertRaises(SystemExit):
            self.plain()
        self.assertIn("employees are free", self.output.getvalue())
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_ephemeral_reason_allows_it(self):
        self.assertTrue(self.plain(ephemeral="no employee_fits"))

    def test_copilot_is_refused_while_claude_is_usable_and_allowed_with_a_reason(self):
        usable = {"runtime": "claude", "installed": True, "enabled": True, "limited": None}
        with patch.object(self.z, "choose_runtime", return_value="copilot"), \
                patch.object(self.z, "runtime_report", return_value=usable):
            with self.assertRaises(SystemExit):
                self.plain(ephemeral="x")
            self.assertIn("claude is usable", self.output.getvalue())
            with patch.object(self.z, "set_copilot_permissions", return_value=None):
                self.assertTrue(self.plain(ephemeral="x", copilot_reason="user asked for copilot"))

    def test_copilot_is_allowed_when_claude_is_limited(self):
        limited = {"runtime": "claude", "installed": True, "enabled": True, "limited": {"note": "cap"}}
        with patch.object(self.z, "choose_runtime", return_value="copilot"), \
                patch.object(self.z, "runtime_report", return_value=limited), \
                patch.object(self.z, "set_copilot_permissions", return_value=None):
            self.assertTrue(self.plain(ephemeral="x"))


class PromptTests(GateCase):
    def setUp(self):
        super().setUp()
        self.sid = self.row("build", "juno", status="running")
        self.conn.execute("UPDATE sessions SET pane_id='p1', agent='juno-t20-build' WHERE id=?", (self.sid,))
        self.conn.commit()

    def state(self, value):
        return patch.object(self.z, "live_agents", return_value={"p1": {"agent_status": value}})

    def test_working_session_is_refused_unless_redirect(self):
        with self.state("working"):
            with self.assertRaises(SystemExit):
                self.z.cmd_prompt([f"S{self.sid}", "hello"])
            self.mock_prompt_worker.assert_not_called()
            self.z.cmd_prompt([f"S{self.sid}", "--redirect", "stop and do X"])
        self.assertEqual(self.mock_prompt_worker.call_args.args, ("juno-t20-build", "stop and do X"))

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


class FinishGateTests(ProfileCase):
    def test_finish_cli_needs_an_idea_or_a_reason_with_a_lesson(self):
        env = {**os.environ, "ZED_PROFILE_DIR": str(self.profile)}
        r = run_script("zed-employee", env, "finish", "--lesson", "x", "-e", "juno")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("script, hook or check", r.stderr + r.stdout)


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


class BbTests(unittest.TestCase):
    def run_bb(self, *argv):
        bb = _load.load("bb")
        calls = []
        with patch.object(bb, "call", side_effect=lambda m, p, body=None: calls.append((m, p, body)) or
                          {"user": {"display_name": "Sam"}, "links": {"html": {"href": "https://bb/c/1"}}}), \
                patch.object(bb.sys, "argv", ["bb", *argv]):
            bb.main()
        return calls

    def test_approve_posts_to_the_approve_endpoint(self):
        self.assertEqual(self.run_bb("pr", "approve", "12", "--repo", "ws/slug"),
                         [("POST", "/repositories/ws/slug/pullrequests/12/approve", None)])

    def test_comment_posts_raw_content(self):
        self.assertEqual(self.run_bb("pr", "comment", "12", "--repo", "ws/slug", "--body", "LGTM"),
                         [("POST", "/repositories/ws/slug/pullrequests/12/comments", {"content": {"raw": "LGTM"}})])

    def test_comment_needs_a_body(self):
        with self.assertRaises(SystemExit):
            self.run_bb("pr", "comment", "12", "--repo", "ws/slug")


if __name__ == "__main__":
    unittest.main()
