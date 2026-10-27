"""Lessons turned into code (T25): merge gate, employees first, Claude first, prompt guard, relay-post, core-sync
check, `bb pr approve|comment`, code-first `finish`. Each gate is tested for the refusal and the allowed case."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _load
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
        self.assertIn('verbatim: "yes, approve it"', text)
        self.assertIn("gh pr review 12 -R o/r --approve", text)
        with self.assertRaises(ValueError):
            zedgates.relay_prompt("", "x")
        with self.assertRaises(ValueError):
            zedgates.relay_prompt("ok", " ")

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
        self.assertIn('verbatim: "Yes, approve PR 7"', sent)
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
