import contextlib
import io
import json
import re
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import _load  # noqa: F401  (drops a worker session's HW_* variables: `start` refuses a worker)


HOME = Path(__file__).resolve().parents[1]


class HwTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.z = types.ModuleType("hw")
        self.z.__file__ = str(HOME / "bin/hw")
        with patch.dict(os.environ, {"HW_PROFILE_DIR": str(self.root)}):
            exec(compile((HOME / "bin/hw").read_text(), self.z.__file__, "exec"), self.z.__dict__)
        (self.root / "profile.md").write_text("# Throwaway test profile\n")
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(contextlib.redirect_stderr(self.output))

    def config(self, data):
        self.z.CLAUDE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.CLAUDE_CONFIG_PATH.write_text(json.dumps(data))

    def test_legacy_lock_identity(self):
        self.assertTrue(self.z.same_holder({"claude_session": "old"}, {"agent_session": "old"}))
        self.assertFalse(self.z.same_holder({"agent_session": "a", "pid": 1},
                                          {"agent_session": "b", "pid": 1}))
        self.assertTrue(self.z.same_holder({"pid": 2}, {"pid": 2}))

    def pane(self, session=None, gone=False, fail=False, missing=False):
        """A fake `herdr pane get` answering for one pane (missing: no herdr binary)."""
        def fake(cmd, check=True, cwd=None):
            if missing:
                raise FileNotFoundError("herdr")
            if gone:
                return subprocess.CompletedProcess(cmd, 1, "", "pane_not_found")
            if fail:
                return subprocess.CompletedProcess(cmd, 127, "", "herdr: not found")
            pane = {"agent_session": {"value": session}} if session else {}
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": {"pane": pane}}), "")
        return patch.object(self.z, "run", side_effect=fake)

    def test_me_takes_the_session_from_the_herdr_pane(self):
        env = {"CLAUDE_PID": "100", "CLAUDE_CODE_SESSION_ID": "helper", "HERDR_PANE_ID": "w1:p1"}
        with patch.dict(os.environ, env, clear=True):
            with self.pane("real"):
                self.assertEqual(self.z.me()["agent_session"], "real")
            with self.pane(None):  # pane without a recorded session: the env id is the fallback
                self.assertEqual(self.z.me()["agent_session"], "helper")
            with self.pane(fail=True):
                self.assertEqual(self.z.me()["agent_session"], "helper")
        with patch.dict(os.environ, {k: v for k, v in env.items() if k != "HERDR_PANE_ID"}, clear=True):
            self.assertEqual(self.z.me()["agent_session"], "helper")  # outside herdr: env only

    def test_holder_alive_trusts_the_pane_not_the_recorded_pid(self):
        holder = {"pane": "w1:p1", "agent_session": "real", "pid": 2 ** 22 + 12345}  # a dead helper pid
        with self.pane("real"):
            self.assertTrue(self.z.holder_alive(holder))
        with self.pane("other"):
            self.assertFalse(self.z.holder_alive(holder))
        with self.pane(gone=True):
            self.assertFalse(self.z.holder_alive(holder | {"pid": os.getpid()}))  # only the pane can say so
        with self.pane(missing=True):  # no herdr binary: the pid decides, no traceback
            self.assertFalse(self.z.holder_alive(holder))
            self.assertTrue(self.z.holder_alive(holder | {"pid": os.getpid()}))
        with self.pane(None):  # no session to compare: the pid decides
            self.assertFalse(self.z.holder_alive(holder))
            self.assertTrue(self.z.holder_alive(holder | {"pid": os.getpid()}))

    def test_lock_survives_a_helper_session_id(self):
        env = {"CLAUDE_PID": str(os.getpid()), "CLAUDE_CODE_SESSION_ID": "helper", "HERDR_PANE_ID": "w1:p1"}
        with patch.dict(os.environ, env, clear=True), self.pane("real"):
            self.z.cmd_lock(["acquire"])
            self.assertEqual(self.z.read_lock()["agent_session"], "real")
            self.assertTrue(self.z.holds_lock())
            self.assertTrue(self.z.holder_alive(self.z.read_lock()))
        with patch.dict(os.environ, env | {"CLAUDE_CODE_SESSION_ID": "another-helper"}, clear=True), self.pane("real"):
            self.assertTrue(self.z.holds_lock())  # a different helper in the same pane is still me
        with patch.dict(os.environ, env | {"HERDR_PANE_ID": "w1:p2"}, clear=True), self.pane("someone-else"):
            self.assertFalse(self.z.holds_lock())  # another pane's session is not me, and is not healed
            self.assertFalse(self.z.holder_alive(self.z.read_lock() | {"pane": "w1:p2"}))

    def test_stale_helper_session_in_the_lock_is_healed(self):
        env = {"CLAUDE_PID": str(os.getpid()), "CLAUDE_CODE_SESSION_ID": "helper", "HERDR_PANE_ID": "w1:p1"}
        stale = {"pane": "w1:p1", "runtime": "claude", "agent_session": "old-helper", "pid": os.getpid()}
        self.z.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.LOCK_PATH.write_text(json.dumps(stale))
        with patch.dict(os.environ, env, clear=True), self.pane("real"):
            self.assertTrue(self.z.holds_lock())
            self.assertEqual(self.z.read_lock()["agent_session"], "real")
        self.z.LOCK_PATH.write_text(json.dumps(stale))
        with patch.dict(os.environ, env, clear=True), self.pane("real"), contextlib.redirect_stdout(io.StringIO()) as out:
            self.z.cmd_lock(["status"])
        self.assertTrue(json.loads(out.getvalue())["you_hold_it"])
        self.assertEqual(self.z.read_lock()["agent_session"], "real")

    def test_heal_copies_session_and_pid_and_drops_the_legacy_key(self):
        env = {"CLAUDE_PID": "4242", "CLAUDE_CODE_SESSION_ID": "helper", "HERDR_PANE_ID": "w1:p1"}
        legacy = {"pane": "w1:p1", "claude_session": "old", "pid": 1, "acquired_at": "t0"}
        self.z.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.LOCK_PATH.write_text(json.dumps(legacy))
        with patch.dict(os.environ, env, clear=True), self.pane("real"), contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(self.z.holds_lock())
        self.assertEqual(self.z.read_lock(), {"pane": "w1:p1", "agent_session": "real",
                                              "pid": 4242, "acquired_at": "t0"})
        self.assertFalse(self.z.LOCK_PATH.with_suffix(".tmp").exists())

    def test_healthy_lock_is_not_rewritten_or_announced(self):
        env = {"CLAUDE_PID": str(os.getpid()), "CLAUDE_CODE_SESSION_ID": "helper", "HERDR_PANE_ID": "w1:p1"}
        healthy = {"pane": "w1:p1", "runtime": "claude", "agent_session": "real", "pid": 1}
        self.z.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.LOCK_PATH.write_text(json.dumps(healthy))
        before = self.z.LOCK_PATH.read_text()
        with patch.dict(os.environ, env, clear=True), self.pane("real"), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertTrue(self.z.holds_lock())
        self.assertEqual(self.z.LOCK_PATH.read_text(), before)
        self.assertEqual(err.getvalue(), "")

    def test_heal_never_writes_a_null_session(self):
        env = {"CLAUDE_PID": str(os.getpid()), "HERDR_PANE_ID": "w1:p1"}  # no env session id
        stale = {"pane": "w1:p1", "runtime": "claude", "agent_session": "old", "pid": os.getpid()}
        self.z.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.LOCK_PATH.write_text(json.dumps(stale))
        with patch.dict(os.environ, env, clear=True), self.pane(None):  # herdr records no session either
            self.z.holds_lock()
        self.assertEqual(self.z.read_lock()["agent_session"], "old")

    def test_heal_leaves_a_lock_that_changed_since_it_was_read(self):
        env = {"CLAUDE_PID": str(os.getpid()), "CLAUDE_CODE_SESSION_ID": "helper", "HERDR_PANE_ID": "w1:p1"}
        stale = {"pane": "w1:p1", "runtime": "claude", "agent_session": "old", "pid": 1}
        taken = {"pane": "w9:p9", "runtime": "claude", "agent_session": "taker", "pid": 2}
        self.z.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.LOCK_PATH.write_text(json.dumps(taken))  # a take-over landed after `stale` was read
        with patch.dict(os.environ, env, clear=True), self.pane("real"):
            self.assertEqual(self.z.heal_lock(stale, self.z.me()), stale)
        self.assertEqual(self.z.read_lock(), taken)

    def test_lock_is_not_healed_for_another_pane_or_session(self):
        env = {"CLAUDE_PID": str(os.getpid()), "CLAUDE_CODE_SESSION_ID": "helper", "HERDR_PANE_ID": "w1:p2"}
        other = {"pane": "w1:p1", "runtime": "claude", "agent_session": "theirs", "pid": os.getpid()}
        self.z.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.LOCK_PATH.write_text(json.dumps(other))
        with patch.dict(os.environ, env, clear=True), self.pane("real"):
            self.assertFalse(self.z.holds_lock())  # other pane
        with patch.dict(os.environ, env | {"HERDR_PANE_ID": "w1:p1"}, clear=True), self.pane(missing=True):
            self.assertFalse(self.z.holds_lock())  # pane session unknown: the env helper id is no proof
        self.assertEqual(self.z.read_lock()["agent_session"], "theirs")

    def test_schema_migrates_old_sessions(self):
        self.z.DB_PATH.parent.mkdir()
        conn = sqlite3.connect(self.z.DB_PATH)
        conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")
        conn.close()
        conn = self.z.db()
        self.addCleanup(conn.close)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
        self.assertTrue({"model", "effort", "brief", "external"} <= columns)
        self.assertTrue({"hat", "verdict", "verdict_tree", "core_start"} <= columns)
        self.assertFalse({"employee", "origin", "verdict_sha"} & columns)  # a new DB has no column for a removed identity
        self.z.db().close()  # migrating twice is harmless

    def test_schema_renames_the_jira_column_to_ticket_and_keeps_its_rows(self):
        self.z.DB_PATH.parent.mkdir()
        conn = sqlite3.connect(self.z.DB_PATH)
        conn.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT NOT NULL, jira TEXT, worktype TEXT, description TEXT, "
                     "status TEXT NOT NULL DEFAULT 'intake', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.executemany("INSERT INTO tasks(id,title,jira,worktype,status,created_at,updated_at) VALUES (?,?,?,'story','active','t0','t1')",
                         [(1, "With a ticket", "ABC-7"), (2, "Without one", None)])
        conn.commit()
        conn.close()
        conn = self.z.db()
        self.addCleanup(conn.close)
        columns = [r["name"] for r in conn.execute("PRAGMA table_info(tasks)")]
        self.assertIn("ticket", columns)
        self.assertNotIn("jira", columns)
        rows = [tuple(r) for r in conn.execute("SELECT id,title,ticket,worktype,status,created_at,updated_at FROM tasks ORDER BY id")]
        self.assertEqual(rows, [(1, "With a ticket", "ABC-7", "story", "active", "t0", "t1"),
                                (2, "Without one", None, "story", "active", "t0", "t1")])
        self.z.db().close()  # migrating twice is harmless
        self.z.cmd_task(["list"])
        self.assertIn("ABC-7", self.output.getvalue())

    def test_task_add_and_set_store_the_ticket(self):
        with patch.object(self.z, "require_lock"):
            self.z.cmd_task(["add", "title=Add a label", "ticket=ABC-7"])
            self.z.cmd_task(["set", "T1", "ticket=ABC-8"])
            with self.assertRaises(SystemExit):
                self.z.cmd_task(["set", "T1", "jira=ABC-9"])
            with self.assertRaises(SystemExit):  # an unknown key is refused, never dropped: the task is not created
                self.z.cmd_task(["add", "title=Old key", "jira=ABC-9"])
        self.assertIn("cannot set 'jira' on a task", self.output.getvalue())
        conn = self.z.db()
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT ticket FROM tasks WHERE id=1").fetchone()["ticket"], "ABC-8")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

    def test_hat_columns_default_to_none(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO tasks (title, created_at, updated_at) VALUES ('t', 'now', 'now')")
        conn.execute("INSERT INTO sessions (task_id, repo, base, branch, worktree, agent, created_at, updated_at) "
                     "VALUES (1, 'r', 'b', 'br', 'w', 'a', 'now', 'now')")
        row = conn.execute("SELECT hat, verdict, verdict_tree, core_start FROM sessions").fetchone()
        self.assertEqual(tuple(row), (None, None, None, None))

    def test_delayed_prompt_activity_does_not_resubmit_the_brief(self):
        for after in ({"agent_status": "working"},
                      {"agent_status": "done", "state_change_seq": 12}):
            with self.subTest(after=after):
                before = {"result": {"agent": {"state_change_seq": 10}}}
                with patch.object(self.z, "herdr_json", side_effect=[before, {"result": {"agent": after}}]), \
                        patch.object(self.z, "run", return_value=subprocess.CompletedProcess(
                            [], 1, "", "agent_prompt_stalled")) as run:
                    self.assertEqual(self.z.prompt_worker("qa", "brief"), (True, ""))
                    run.assert_called_once()

    def fake_prompt(self, states, stalled=True):
        """prompt_worker against a fake herdr: `agent prompt` stalls (or succeeds), each later `agent get` returns the
        next of `states`, and Enter presses are counted. Returns (result, enter presses, queued wake events)."""
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[2] == "prompt":
                return subprocess.CompletedProcess(cmd, 1 if stalled else 0, "", "agent_prompt_stalled" if stalled else "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        gets = iter([{"state_change_seq": 10}, *states])
        get = lambda args: {"result": {"agent": next(gets, states[-1] if states else None)}}
        with patch.object(self.z, "run", side_effect=run), patch.object(self.z, "herdr_json", side_effect=get), \
                patch.object(self.z.time, "sleep"):
            result = self.z.prompt_worker("qa", "brief")
        enters = [c for c in calls if c[2:] == ["send-keys", "qa", "enter"]]
        return result, len(enters), [e["text"] for e in self.z.queue_pending()]

    def test_prompt_picked_up_needs_no_action(self):
        result, enters, queued = self.fake_prompt([], stalled=False)
        self.assertEqual((result, enters, queued), ((True, ""), 0, []))

    def test_unsent_prompt_gets_one_enter(self):
        idle = {"agent_status": "idle", "state_change_seq": 10}
        result, enters, queued = self.fake_prompt([*[idle] * 40, {"agent_status": "working", "state_change_seq": 11}])
        self.assertEqual((result, enters, queued), ((True, ""), 1, []))

    def test_prompt_still_idle_after_enter_wakes_hw_without_retrying(self):
        idle = {"agent_status": "idle", "state_change_seq": 10}
        result, enters, queued = self.fake_prompt([idle])
        self.assertEqual((result[0], enters), (False, 1))
        self.assertEqual(len(queued), 1)
        self.assertIn("qa · prompt not picked up", queued[0])

    def test_prompt_failure_is_reported(self):
        with patch.object(self.z, "herdr_json", return_value={"result": {"agent": {}}}), \
                patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 1, "", "agent_blocked")):
            self.assertEqual(self.z.prompt_worker("qa", "brief"), (False, "agent_blocked"))

    def session(self):
        conn = self.z.db()
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO tasks(title,created_at,updated_at) VALUES ('QA','now','now')")
        conn.execute("INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,workspace_id,pane_id,"
                     "brief,plan,created_at,updated_at) VALUES (1,'scratch','main','feature/qa',?,'qa','qa-ws','qa:p1',"
                     "?,?,'2000-01-01 00:00','2000-01-01 00:00')",
                     (str(self.root), str(self.root / "profile.md"), str(self.root / "briefs" / "T1-scratch-plan.md")))
        conn.commit()
        return conn

    def test_closing_a_workspace_that_is_already_gone_succeeds(self):
        gone = subprocess.CompletedProcess([], 1, '{"error":{"code":"workspace_not_found"}}', "")
        with patch.object(self.z, "run", return_value=gone):
            self.z.close_workspace("w1")
        with patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 1, "", "server_unavailable")):
            with self.assertRaises(SystemExit):
                self.z.close_workspace("w1")

    def test_watch_reports_fast_turns_once_and_records_first_idle(self):
        self.session()
        agent = {"agent_status": "idle", "state_change_seq": 10}
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "live_agents", return_value={"qa:p1": agent}), \
                patch.object(self.z, "read_screen", return_value="HW:QUESTION\nWhich greeting?") as screen:
            self.z.cmd_watch(["--once", "0.01"])
            self.assertIn("at watch start", self.output.getvalue())
            self.z.cmd_ack(["--all"])  # handled; an un-acked event would be shown again
            self.output.truncate(0)
            self.output.seek(0)
            with patch.object(self.z.time, "sleep", side_effect=InterruptedError):
                with self.assertRaises(InterruptedError):
                    self.z.cmd_watch(["--once", "0.01"])
            self.assertEqual(self.output.getvalue(), "")
            agent["state_change_seq"] = 12
            screen.return_value = "HW:QUESTION\nWhich colour?"  # a repeated block would not be an event
            self.z.cmd_watch(["--once"])
            self.assertIn("qa", self.output.getvalue())

    def test_watch_exits_when_lock_is_lost(self):
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=False), \
                patch.object(self.z, "read_lock", return_value=None):
            with self.assertRaises(SystemExit) as error:
                self.z.cmd_watch(["--once"])
        self.assertEqual(error.exception.code, 3)
        self.assertIn("LOCK LOST", self.output.getvalue())

    DONE_SCREEN = """⏺ Done with the work.

  HW:DONE
  Summary: Added the digest label
  Branch: story/abc-1-x  Commits: abc123
  PR: https://github.com/org/repo/pull/7  (draft)
  QA: playwright-cli on localhost, happy path and empty digest
  Follow-ups: none
  Learnings: run npm ci first
    then npm test
────────────────────────
❯ 
  ⏵⏵ accept edits on"""

    def test_a_block_ends_at_opencodes_chrome(self):
        """What `herdr agent read` showed of OpenCode v2.2.7 (2026-11-12) after a turn that ended in a question: without
        an end at its chrome the last item took the footer with it and was recorded as a second question."""
        screen = ("  ┃  Formatting test. Reply with exactly these four lines:\n  ┃  HW:QUESTION" + " " * 60 + "10,605 tokens\n"
                  "  ┃  1. Not this one: it is the echo of a prompt\n  ┃\n\n     Noted.\n     HW:QUESTION\n\n"
                  "     1. Red or blue? I recommend red.\n     2. Round or square corners?\n\n"
                  "     Build · Claude Haiku 5.5 · 1.6s · 26.7 tok/s\n\n\n  ┃\n  ┃\n  ┃\n"
                  "  ┃  Build auto · Claude Haiku 5.5 GitHub Copilot · low\n  ╹▀▀▀▀▀▀▀▀\n  /…/work  10.6K (1%) · $0.00  ctrl+p commands\n")
        self.assertEqual(self.z.parse_block(screen)["items"], ["Red or blue? I recommend red.", "Round or square corners?"])
        done = self.z.parse_block("     HW:DONE\n     Summary: Added it\n     QA: ran it\n\n     Build · Some Model · 2m 13s\n  ┃\n")
        self.assertEqual(done["fields"], {"Summary": "Added it", "QA": "ran it"})

    def test_parse_block_kinds_and_chrome(self):
        b = self.z.parse_block(self.DONE_SCREEN)
        self.assertEqual(b["kind"], "DONE")
        self.assertEqual(b["fields"]["PR"], "https://github.com/org/repo/pull/7  (draft)")
        self.assertEqual(b["fields"]["Learnings"], "run npm ci first\nthen npm test")
        self.assertNotIn("accept edits", b["text"])
        q = self.z.parse_block("HW:QUESTION\n1. Which greeting?\n   Hello or Hi.\n2. Which colour?\n```\n")
        self.assertEqual(q["items"], ["Which greeting?\nHello or Hi.", "Which colour?"])
        self.assertEqual(self.z.parse_block("HW:BLOCKED\nNeed credentials")["items"], ["Need credentials"])
        self.assertIsNone(self.z.parse_block("no marker here, just prose mentioning HW:DONE inline"))
        self.assertIsNone(self.z.parse_block("HW:DONE\n" + "line\n" * 100))
        last = self.z.parse_block("HW:QUESTION\n1. old\n\nHW:DONE\nSummary: s\nQA: q")
        self.assertEqual(last["kind"], "DONE")

    def test_record_block_done_sets_pr_status_and_flags_missing_qa(self):
        conn = self.session()
        conn.execute("UPDATE tasks SET status='active'")
        s = conn.execute("SELECT * FROM sessions").fetchone()
        text = self.z.record_block(conn, s, self.z.parse_block(self.DONE_SCREEN))
        self.assertIn("PR: https://github.com/org/repo/pull/7", text)
        self.assertNotIn("⚠", text)
        row = conn.execute("SELECT s.pr_url, s.status, t.status FROM sessions s JOIN tasks t ON t.id=s.task_id").fetchone()
        self.assertEqual(tuple(row), ("https://github.com/org/repo/pull/7", "finished", "pr_open"))
        conn.execute("UPDATE tasks SET status='active'")
        noqa = self.z.parse_block("HW:DONE\nSummary: s2\nPR: https://github.com/o/r/pull/8\nQA: none")
        self.assertIn("⚠", self.z.record_block(conn, s, noqa))
        self.assertEqual(conn.execute("SELECT status FROM tasks").fetchone()[0], "active")

    def test_record_block_questions_are_recorded_once(self):
        conn = self.session()
        s = conn.execute("SELECT * FROM sessions").fetchone()
        block = self.z.parse_block("HW:QUESTION\n1. Which greeting?\n2. Which colour?")
        self.assertIn("Q1: Which greeting?", self.z.record_block(conn, s, block))
        again = self.z.record_block(conn, s, block)
        self.assertIn("already recorded", again)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT status FROM sessions").fetchone()[0], "waiting_user")

    def settle(self, screen, alerts, **kw):
        conn = self.session() if not hasattr(self, "_conn") else self._conn
        self._conn = conn
        s = conn.execute("SELECT * FROM sessions").fetchone()
        with patch.object(self.z, "read_screen", return_value=screen):
            return self.z.settle_turn(conn, s, screen, alerts, "S1 T1 qa → idle", 5), s

    def test_settle_turn_surfaces_a_new_block_once_then_treats_it_as_a_lapse(self):
        alerts = {}
        line, s = self.settle(self.DONE_SCREEN, alerts)
        self.assertIn("HW:DONE", line)
        line, _ = self.settle(self.DONE_SCREEN, alerts)  # same block, still idle: nothing new
        self.assertIsNone(line)
        self.assertIn("lapse", alerts[s["id"]])

    def test_protocol_lapse_nudges_once_and_wakes_on_repeat(self):
        conn = self.session()
        s = conn.execute("SELECT * FROM sessions").fetchone()
        alerts = {1: {"lapse": {"seq": 5, "since": 0}}}
        with patch.object(self.z, "run") as run:
            self.assertEqual(self.z.lapse_tick(s, "idle", 5, alerts)[0], "nudge")
            self.assertIn("HW:QUESTION", run.call_args.args[0][-1])
            self.assertIsNone(self.z.lapse_tick(s, "idle", 5, alerts))  # lapse consumed
            alerts[1]["lapse"] = {"seq": 6, "since": 0}
            self.assertEqual(self.z.lapse_tick(s, "idle", 6, alerts)[0], "wake")
            run.assert_called_once()  # no second nudge
        alerts[1]["lapse"] = {"seq": 7, "since": 0}
        self.assertIsNone(self.z.lapse_tick(s, "working", 7, alerts))  # woke up on its own
        alerts[1]["lapse"] = {"seq": 7, "since": 10 ** 12}
        self.assertIsNone(self.z.lapse_tick(s, "idle", 7, alerts))  # still inside the grace period

    def test_watch_records_the_block_and_puts_it_in_the_event(self):
        conn = self.session()
        agent = {"agent_status": "idle", "state_change_seq": 10}
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "live_agents", return_value={"qa:p1": agent}), \
                patch.object(self.z, "read_screen", return_value=self.DONE_SCREEN):
            self.z.cmd_watch(["--once", "0.01"])
        out = self.output.getvalue()
        self.assertIn("HW:DONE · Added the digest label", out)
        self.assertEqual(conn.execute("SELECT pr_url FROM sessions").fetchone()[0], "https://github.com/org/repo/pull/7")

    def test_brief_status_is_bounded_and_start_prints_a_digest(self):
        conn = self.session()
        for i in range(20):
            conn.execute("INSERT INTO questions(task_id,session_id,question,asked_at) VALUES (1,1,?,'now')", (f"q{i}",))
        conn.commit()
        brief = "\n".join(self.z.render_brief(conn, live={"qa:p1": {"agent_status": "idle"}}))
        self.assertIn("8 more omitted", brief)
        self.assertIn("S1 T1 qa running/idle", brief)
        with patch.object(self.z, "cmd_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "live_agents", return_value={}), patch.object(self.z, "watcher_alive", return_value=None):
            self.output.truncate(0)
            self.z.cmd_start([])
        self.assertIn("watcher: NOT RUNNING", self.output.getvalue())

    def test_second_watcher_is_refused(self):
        with patch.object(self.z, "require_lock"), patch.object(self.z, "claim_watcher", return_value=False), \
                patch.object(self.z, "watcher_alive", return_value=999):
            with self.assertRaises(SystemExit) as e:
                self.z.cmd_watch(["--once"])
        self.assertEqual(e.exception.code, 4)
        self.assertIn("WATCH_ALREADY_RUNNING", self.output.getvalue())

    def test_brief_scaffold_and_spawn_refuses_placeholders(self):
        conn = self.session()
        conn.execute("UPDATE tasks SET ticket='ABC-9', description='Add a label'")
        conn.commit()
        (self.root / "config").mkdir(exist_ok=True)
        (self.root / "config" / "projects.json").write_text(json.dumps({
            "worktree_root": str(self.root / "wt"), "repos": {"r": {"path": str(self.root / "r"), "role": "backend"}}}))
        notes = self.root / "knowledge" / "repos"
        notes.mkdir(parents=True)
        (notes / "r.md").write_text("# r\n## Setup\nnpm ci\n## History\nold stuff\n## Test logins\nadmin / from vault\n")
        self.z.cmd_brief(["T1", "repo=r", "base=main", "branch=story/abc-9-label"])
        brief = (self.root / "briefs" / "T1-r.md").read_text()
        self.assertIn("ABC-9", brief)
        self.assertIn("admin / from vault", brief)
        self.assertNotIn("old stuff", brief)
        self.assertNotIn("{{", brief)
        self.assertIn("Add a label", brief)
        with patch.object(self.z, "require_lock"):
            with self.assertRaises(SystemExit):
                self.z.cmd_brief(["T1", "repo=r", "base=main", "branch=b"])  # exists, no force
            with self.assertRaises(SystemExit):
                self.z.cmd_spawn(["T1", "repo=r", "base=main", "branch=b", f"brief={self.root / 'briefs' / 'T1-r.md'}"])
        self.assertIn("unfilled placeholder", self.output.getvalue())

    def test_close_appends_to_the_spend_ledger_and_status_shows_spend_against_plan(self):
        conn = self.session()
        s = conn.execute("SELECT * FROM sessions").fetchone()
        (self.root / "briefs").mkdir(exist_ok=True)
        (self.root / "briefs" / "T1-scratch-plan.md").write_text("- [x] a\n- [x] b\n- [ ] c\n")
        usage = {"output": 400_000, "input": 10, "cache_read": 5, "cache_write": 7}
        with patch.object(self.z, "claude_usage", return_value=usage):
            self.z.record_spend(conn, s)
            rows = self.z.spend_rows()
            self.assertEqual((rows[0]["session"], rows[0]["plan_done"], rows[0]["plan_total"]), (1, 2, 3))
            self.assertEqual(rows[0]["tokens"]["output"], 400_000)
            status = self.z.render_status(conn, live={})
        self.assertIn("spend (closed sessions): 0.40M output tokens", status)
        self.assertIn("plan 2/3 steps", status)
        (self.root / "briefs" / "T1-scratch-plan.md").write_text("- [x] a\n- [x] b\n- [ ] Raise the PR\n")
        status = self.z.render_status(conn, live={})
        self.assertIn("2/2 steps done (+1 awaiting merge/PR)", status)
        (self.root / "briefs" / "T1-scratch-plan.md").write_text("- [x] a\n- [x] b\n- [ ] c\n")
        self.assertIn("200k output tokens per step", status)
        conn.execute("UPDATE sessions SET external=1")
        self.z.record_spend(conn, conn.execute("SELECT * FROM sessions").fetchone())
        self.assertEqual(len(self.z.spend_rows()), 1)  # external sessions aren't ours to account

    def hook(self, mode, data=None, lock=True, tracked=True, watcher=None, watch=None):
        """Run `hw hook <mode>`; returns the exit code (0 when it simply returns)."""
        with patch.object(self.z, "hook_input", return_value=data or {}), \
                patch.object(self.z, "holds_lock", return_value=lock), \
                patch.object(self.z, "has_tracked_work", return_value=tracked), \
                patch.object(self.z, "watcher_alive", return_value=watcher), \
                patch.object(self.z, "cmd_watch", side_effect=watch or (lambda args: False)) as cmd_watch, \
                patch.object(self.z, "GUARD_WAIT", 0), patch.object(self.z, "ARM_MIN_SECONDS", 0):
            try:
                self.z.cmd_hook([mode])
            except SystemExit as e:
                return e.code, cmd_watch
            return 0, cmd_watch

    def test_stop_guard_blocks_a_blind_turn_end_once(self):
        self.assertEqual(self.hook("stop-guard")[0], 2)
        self.assertIn("no watcher is running", self.output.getvalue())
        self.assertEqual(self.hook("stop-guard", data={"stop_hook_active": True})[0], 0)  # never loops
        self.assertEqual(self.hook("stop-guard", watcher=123)[0], 0)
        self.z.ARM_STAMP_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.ARM_STAMP_PATH.touch()  # arm just ran (and woke Herdwright with an event): nothing to block
        self.assertEqual(self.hook("stop-guard")[0], 0)
        os.utime(self.z.ARM_STAMP_PATH, (1, 1))  # an old stamp doesn't count
        self.assertEqual(self.hook("stop-guard")[0], 2)
        self.assertEqual(self.hook("stop-guard", lock=False)[0], 0)  # read-only sessions are untouched
        self.assertEqual(self.hook("stop-guard", tracked=False)[0], 0)

    def test_arm_hook_wakes_only_on_events_and_only_when_it_may(self):
        code, cmd_watch = self.hook("arm", watch=lambda args: True)
        self.assertEqual(code, 2)
        self.assertEqual(cmd_watch.call_args.args[0], ["--once", "--exit-when-idle"])
        self.assertEqual(self.hook("arm", watch=lambda args: False)[0], 0)  # went idle: no wake
        self.assertEqual(self.hook("arm", watch=lambda args: sys.exit(3))[0], 2)  # lock lost: tell Herdwright
        self.assertEqual(self.hook("arm", watch=lambda args: sys.exit(4))[0], 0)  # lost the race to another watcher
        for kw in ({"lock": False}, {"tracked": False}, {"watcher": 123}):
            code, cmd_watch = self.hook("arm", **kw)
            self.assertEqual(code, 0)
            cmd_watch.assert_not_called()

    # T115 on main, ported (T121): an event is shown again by Herdwright's turns, not at a watcher's start or by a timer

    def stop(self, **kw):
        """The Stop hooks of a turn's end, as Claude Code runs them: returns arm's exit code."""
        self.hook("stop-guard", **kw)
        return self.hook("arm", **kw)[0]

    def stop_with_the_real_watcher(self):
        """A Stop whose arm hook runs the real one-shot watcher (no worker tracked, so it goes idle at once): where
        4666ee2 showed un-acked events again, at the watcher's start."""
        real = self.z.cmd_watch
        with patch.object(self.z, "require_lock"), patch.object(self.z, "claim_watcher", return_value=True), \
                patch.object(self.z, "live_agents", return_value={}), patch.object(self.z.time, "sleep"):
            return self.hook("arm", watch=real)[0]

    def shown_again(self):
        return re.findall(r"\[E(\d+)\] \(again", self.output.getvalue())

    def test_an_event_delivered_mid_turn_and_acked_in_the_next_turn_is_never_shown_again(self):
        """The two real cases on main (E596, E597): delivered while a turn ran, acked in the next one, any delay."""
        self.hook("turn-start")  # the user's prompt starts a turn
        self.z.queue_add(["S352 T107 qa-t107 · status file: HW:DONE"])  # a background watcher reports mid-turn
        self.assertEqual(self.stop_with_the_real_watcher(), 0)  # the running turn ends: Herdwright has not read it yet
        self.assertEqual(self.shown_again(), [])
        self.hook("turn-start")
        with patch.object(self.z, "require_lock"):
            self.z.cmd_ack(["E1"])
        for _ in range(3):
            self.assertEqual(self.stop_with_the_real_watcher(), 0)
            self.hook("turn-start")
        self.assertEqual(self.shown_again(), [])

    def test_an_event_never_acked_is_shown_at_the_end_of_the_next_full_turn_whatever_else_arrives(self):
        self.z.queue_turn_started()
        self.z.queue_add(["S1 asks"])  # mid-turn
        self.assertEqual(self.stop(), 0)
        self.z.queue_add(["S2 asks", "S3 asks"])  # one wake reaches the idle Herdwright and starts the next turn
        self.z.queue_add(["S4 asks"])  # arrives while that turn runs: it is read only in the turn after
        self.assertEqual(self.stop(), 2)  # that turn ends with none acked: those it had in front of it are due
        self.assertEqual(self.shown_again(), ["1", "2", "3"])

    def test_an_event_that_woke_an_idle_herdwright_is_due_after_the_one_turn_it_started(self):
        """S368 finding 1: an idle delivery waited a second Stop."""
        self.z.queue_end_turn()
        self.z.queue_add(["S1 asks"])  # the Stop hook's watcher wakes the idle Herdwright with it
        self.assertEqual(self.stop(), 2)
        self.assertEqual(self.shown_again(), ["1"])

    def test_a_stop_blocked_by_another_hook_is_one_turn_not_two(self):
        """S368 finding 2: Claude Code fires Stop again in the same turn when a Stop hook blocked the first."""
        self.z.queue_turn_started()
        self.z.queue_add(["S1 asks"])  # mid-turn
        self.assertEqual(self.stop(), 0)
        self.assertEqual(self.stop(data={"stop_hook_active": True}), 0)  # the same turn's second Stop counts nothing
        self.assertEqual(self.shown_again(), [])
        self.z.queue_turn_started()
        self.assertEqual(self.stop(), 2)  # the next full turn does
        self.assertEqual(self.shown_again(), ["1"])

    def test_an_ack_between_the_stop_and_the_print_wins(self):
        self.z.queue_end_turn()
        self.z.queue_add(["S1 asks"])

        def ack_meanwhile(seconds):
            with patch.object(self.z, "require_lock"):
                self.z.cmd_ack(["E1"])
        with patch.object(self.z.time, "sleep", side_effect=ack_meanwhile):
            code, cmd_watch = self.hook("arm")
        self.assertEqual((code, self.shown_again()), (0, []))
        cmd_watch.assert_called_once()  # nothing to show again: the hook arms the watcher as usual

    def test_turn_start_marks_a_turn_only_for_the_lock_holder_and_an_old_queue_counts_its_first_stop(self):
        self.z.WAKE_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.z.WAKE_QUEUE_PATH.write_text(json.dumps({"next": 2, "events": [  # as the queue is on the day this lands
            {"id": 1, "ts": "2026-11-12 09:43", "text": "S1 asks", "shown": 1, "shown_at": 1, "acked": False}]}))
        self.assertEqual(self.stop(), 2)
        self.assertEqual(self.shown_again(), ["1"])
        self.assertEqual(self.hook("turn-start", lock=False)[0], 0)
        with self.z.wake_queue() as q:
            self.assertTrue(q["running"])  # the re-show woke Herdwright: that turn counts
            q["running"] = False
        self.hook("turn-start", lock=False)
        with self.z.wake_queue() as q:
            self.assertFalse(q["running"])  # a read-only session's prompts are not Herdwright's turns
        self.hook("turn-start")
        with self.z.wake_queue() as q:
            self.assertTrue(q["running"])

    def test_start_lists_every_unacked_event_at_once(self):
        self.z.queue_turn_started()
        self.z.queue_add(["S1 asks", "S2 asks"])
        self.assertEqual([e["id"] for e in self.z.queue_pending()], [1, 2])

    def test_broken_hook_never_blocks(self):
        code, _ = self.hook("arm", watch=lambda args: 1 / 0)
        self.assertEqual(code, 1)

    def test_one_watcher_at_a_time(self):
        self.assertIsNone(self.z.watcher_alive())
        self.assertTrue(self.z.claim_watcher())
        self.addCleanup(self.z._watcher_lock.close)
        self.assertEqual(self.z.watcher_alive(), os.getpid())
        self.assertTrue(self.z.claim_watcher())  # idempotent in the watcher itself
        other = subprocess.run([sys.executable, "-c", (
            "import fcntl,sys; f=open(sys.argv[1],'a')\n"
            "try: fcntl.flock(f, fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
            "except BlockingIOError: sys.exit(7)"), str(self.z.WATCHER_LOCK_PATH)])
        self.assertEqual(other.returncode, 7)  # another process can't claim it

    def test_block_printed_while_still_working_is_reported_once(self):
        """S19 printed an HW:QUESTION and stayed 'working' (waiting on a background workflow)."""
        conn = self.session()
        s = conn.execute("SELECT * FROM sessions").fetchone()
        alerts = {}
        question = "HW:QUESTION\n1. Which greeting?"
        with patch.object(self.z, "WORKING_SCAN", 0), patch.object(self.z, "read_screen") as screen:
            screen.return_value = "HW:QUESTION\n1. Which gr"  # half printed
            self.assertIsNone(self.z.scan_working(conn, s, alerts))
            screen.return_value = question
            self.assertIsNone(self.z.scan_working(conn, s, alerts))  # changed since last look: wait for it to settle
            line = self.z.scan_working(conn, s, alerts)
            self.assertIn("new HW:QUESTION while still working", line)
            self.assertIn("Q1: Which greeting?", line)
            self.assertIsNone(self.z.scan_working(conn, s, alerts))  # same block: not reported again
            self.assertIsNone(self.z.scan_working(conn, s, alerts))
            screen.return_value = "just working output"
            self.assertIsNone(self.z.scan_working(conn, s, alerts))
        with patch.object(self.z, "read_screen", return_value="HW:QUESTION\n1. Another?"):
            self.assertIsNone(self.z.scan_working(conn, s, alerts))  # throttled by WORKING_SCAN
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 1)

    def test_watch_reports_a_block_from_a_working_worker(self):
        conn = self.session()
        conn.execute("UPDATE sessions SET created_at=?", (self.z.now(),))  # recent: no STALL event
        conn.commit()
        agent = {"agent_status": "working", "state_change_seq": 3}
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "claim_watcher", return_value=True), patch.object(self.z, "WORKING_SCAN", 0), \
                patch.object(self.z, "live_agents", return_value={"qa:p1": agent}), \
                patch.object(self.z, "read_screen", return_value="HW:BLOCKED\nNeed a login"), \
                patch.object(self.z.time, "sleep"):
            self.z.cmd_watch(["--once", "0.01"])
        self.assertIn("new HW:BLOCKED while still working", self.output.getvalue())
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 1)

    def test_status_file_is_read_in_any_state_and_not_reported_twice(self):
        conn = self.session()
        conn.execute("UPDATE sessions SET created_at=?", (self.z.now(),))
        conn.commit()
        s = conn.execute("SELECT * FROM sessions").fetchone()
        self.z.STATUS_DIR.mkdir(parents=True)
        path = self.z.STATUS_DIR / "S1.log"
        path.write_text("needs-decision: Which greeting?\nneeds-decision: Which colour?\ndone: half writ")
        alerts = {}
        lines = self.z.new_status_lines(s, alerts)
        self.assertEqual(lines, ["needs-decision: Which greeting?", "needs-decision: Which colour?"])  # no newline yet
        blocks = self.z.status_blocks(lines)
        self.assertEqual((blocks[0]["kind"], blocks[0]["items"]), ("QUESTION", ["Which greeting?", "Which colour?"]))
        self.z.record_block(conn, s, blocks[0])
        path.write_text(path.read_text() + "ten\nblocked: Need a login\n")
        more = self.z.status_blocks(self.z.new_status_lines(s, alerts))
        self.assertEqual([b["kind"] for b in more], ["DONE", "BLOCKED"])
        self.assertEqual(self.z.new_status_lines(s, alerts), [])
        done = self.z.status_blocks(["done: Added label; pr=https://github.com/o/r/pull/3; qa=playwright, happy path"])[0]
        self.assertEqual(done["fields"], {"Summary": "Added label", "PR": "https://github.com/o/r/pull/3",
                                          "QA": "playwright, happy path"})
        # the same question later printed as an HW block is already known: no second event
        self.assertTrue(self.z.block_known(conn, s, self.z.parse_block("HW:QUESTION\n1. Which greeting?")))
        conn.commit()
        self._conn = conn
        line, _ = self.settle("HW:QUESTION\n1. Which greeting?", {})
        self.assertIsNone(line)

    def test_watch_reports_status_file_lines_while_working(self):
        conn = self.session()
        conn.execute("UPDATE sessions SET created_at=?", (self.z.now(),))
        conn.commit()
        self.z.STATUS_DIR.mkdir(parents=True)
        (self.z.STATUS_DIR / "S1.log").write_text("needs-decision: Which greeting?\n")
        agent = {"agent_status": "working", "state_change_seq": 3}
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "claim_watcher", return_value=True), \
                patch.object(self.z, "live_agents", return_value={"qa:p1": agent}), \
                patch.object(self.z, "read_screen", return_value="busy"):
            self.z.cmd_watch(["--once", "0.01"])
        self.assertIn("status file: HW:QUESTION", self.output.getvalue())
        self.assertIn("Q1: Which greeting?", self.output.getvalue())

    def test_wake_queue_keeps_events_until_acked_and_never_loops_forever(self):
        self.assertEqual(self.z.queue_add(["S1 finished", "S2 asks"]), ["[E1] S1 finished", "[E2] S2 asks"])
        with patch.object(self.z, "require_lock"):
            self.end_turns(2)
            self.assertEqual(len(self.z.queue_represent()), 2)  # shown a 2nd time (a crash lost the 1st)
            self.end_turns(1)
            self.assertEqual(len(self.z.queue_represent()), 2)  # a 3rd
            self.end_turns(5)
            self.assertEqual(self.z.queue_represent(), [])  # capped: a forgotten ack can't loop the hook
            self.assertEqual(len(self.z.queue_pending()), 2)  # but `start` still lists them
            self.z.cmd_ack(["E1"])
            self.assertEqual([e["id"] for e in self.z.queue_pending()], [2])
            self.z.cmd_ack(["--all"])
            self.assertEqual(self.z.queue_pending(), [])
            with self.assertRaises(SystemExit):
                self.z.cmd_ack(["E99"])

    def end_turns(self, n):
        """`n` turns of Herdwright begin (the user's prompt) and end (Stop)."""
        for _ in range(n):
            self.z.queue_turn_started()
            self.z.queue_end_turn()

    def test_watch_represents_unacked_events_before_anything_else(self):
        self.session()
        self.z.queue_add(["S1 T1 qa asked something"])
        self.end_turns(2)
        with patch.object(self.z, "require_lock"), patch.object(self.z, "claim_watcher", return_value=True), \
                patch.object(self.z, "live_agents", side_effect=AssertionError("must not poll")):
            self.assertTrue(self.z.cmd_watch(["--once"]))
        self.assertIn("[E1] (again, un-acked", self.output.getvalue())
        self.assertIn("asked something", self.output.getvalue())

    def test_watch_events_are_queued_with_ids(self):
        conn = self.session()
        agent = {"agent_status": "idle", "state_change_seq": 10}
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "claim_watcher", return_value=True), \
                patch.object(self.z, "live_agents", return_value={"qa:p1": agent}), \
                patch.object(self.z, "read_screen", return_value=self.DONE_SCREEN):
            self.z.cmd_watch(["--once", "0.01"])
        self.assertRegex(self.output.getvalue(), r"\[E1\] S1 T1 qa is idle")
        self.assertEqual(len(self.z.queue_pending()), 1)

    def test_model_and_effort_come_from_claude_json_capped_by_its_ceiling(self):
        self.assertEqual(self.z.pick_model("story"), (None, None))  # nothing set, nothing passed: Claude Code's own default
        self.config({"model": "claude-opus-5-5", "effort": "high", "effort_by_kind": {"bug": "medium", "design": "max"}})
        self.assertEqual(self.z.pick_model("bug"), ("claude-opus-5-5", "medium"))
        self.assertEqual(self.z.pick_model("design"), ("claude-opus-5-5", "high"))  # never above the ceiling
        self.assertEqual(self.z.pick_model("unlisted"), ("claude-opus-5-5", "high"))
        self.config({"effort_by_kind": {"design": "max"}})  # no ceiling set: the kind's effort as written, no model
        self.assertEqual((self.z.pick_model("design"), self.z.pick_model("bug")), ((None, "max"), (None, None)))
        self.config({"effort": "extreme"})
        with self.assertRaises(SystemExit):
            self.z.pick_model("bug")
        self.assertIn("'extreme' is not an effort of Claude Code: low, medium, high, xhigh, max. Set it with `bin/hw configure --effort <effort>`",
                      self.output.getvalue())

    def test_max_running_counts_open_sessions_of_the_runtime_and_queues_the_rest(self):
        self.config({})
        (self.root / "config" / "settings.json").write_text(json.dumps({"runtime": "claude"}))
        self.z.cmd_configure(["--max-sessions", "2"])  # as the assistant stores what the user asked for
        self.z.OPENCODE_CONFIG_PATH.write_text(json.dumps({"max_running": 1}))
        agents = {"w:1": {"agent": "claude"}, "w:2": {"agent": "claude"}, "w:3": {"agent": "opencode"}}
        with patch.object(self.z, "live_agents", return_value=agents):
            self.assertEqual((self.z.running_count(None, "claude"), self.z.running_count(None, "opencode")), (2, 1))
            for runtime, said in (("claude", "2 Claude Code sessions are open"), ("opencode", "1 OpenCode sessions are open")):
                with self.assertRaises(SystemExit) as e:
                    self.z.require_capacity(None, 7, runtime)
                self.assertEqual(e.exception.code, 75)
                self.assertIn(f"hw: queued: {said}", self.output.getvalue())
            self.config({})  # no limit set: nothing is queued
            self.z.require_capacity(None, 7, "claude")

    def test_context_budget_check(self):
        self.z.HW_HOME = self.root / "home"
        self.z.HW_HOME.mkdir()
        (self.z.HW_HOME / "AGENTS.md").write_text("word " * 60)
        (self.z.HW_HOME / "CLAUDE.md").write_text("@AGENTS.md\n")  # the import is not counted twice
        (self.root / "lessons.md").write_text("word " * 30)
        words, budget = self.z.context_words()
        self.assertEqual((words["AGENTS.md"], words["lessons.md"], budget), (60, 30, 4000))
        self.assertNotIn("CLAUDE.md", words)
        self.assertIn("94 of 4000 words", self.z.context_line())
        (self.root / "config").mkdir(exist_ok=True)
        (self.root / "config" / "settings.json").write_text('{"context_budget_words": 90}')
        self.assertIn("OVER BUDGET", self.z.context_line())
        (self.root / "config" / "settings.json").write_text('{"context_budget_words": 94}')
        self.assertNotIn("OVER BUDGET", self.z.context_line())

    def git_worktree(self):
        """A throwaway clone with an upstream, clean and pushed."""
        remote, work = self.root / "remote.git", self.root / "work"
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        for cmd in (["git", "init", "-q", "--bare", str(remote)], ["git", "init", "-q", "-b", "feature/qa", str(work)]):
            subprocess.run(cmd, check=True, env=env)
        (work / "a.txt").write_text("a")
        for cmd in (["add", "."], ["commit", "-qm", "a"], ["remote", "add", "origin", str(remote)], ["push", "-qu", "origin", "feature/qa"]):
            subprocess.run(["git", "-C", str(work), *cmd], check=True, env=env)
        return work, env

    def test_landed_work_test_lists_exactly_what_blocks_closing(self):
        conn = self.session()
        work, env = self.git_worktree()
        conn.execute("UPDATE sessions SET worktree=?", (str(work),))
        conn.commit()
        s = lambda: conn.execute("SELECT * FROM sessions").fetchone()
        self.assertEqual(self.z.landed_blockers(conn, s()), [])
        (work / "b.txt").write_text("b")
        (work / "a.txt").write_text("changed")
        subprocess.run(["git", "-C", str(work), "commit", "-qam", "local"], check=True, env=env)
        (work / "c.txt").write_text("c")
        conn.execute("UPDATE sessions SET pr_url='https://github.com/o/r/pull/1'")
        conn.execute("INSERT INTO questions(task_id,session_id,question,asked_at) VALUES (1,1,'q','now')")
        (self.root / "briefs").mkdir(exist_ok=True)
        (self.root / "briefs" / "T1-scratch-plan.md").write_text("- [x] a\n- [ ] b\n")
        with patch.object(self.z, "pr_snapshot", return_value={"state": "open", "reviews": 0, "checks": None}):
            blockers = self.z.landed_blockers(conn, s())
        text = "\n".join(blockers)
        for part in ("2 uncommitted file(s): b.txt, c.txt", "1 commit(s) not pushed", "PR still open", "plan has 1 of 2 steps left",
                     "1 open question(s)"):
            self.assertIn(part, text)
        with patch.object(self.z, "pr_snapshot", return_value={"state": "merged", "reviews": 0, "checks": None}):
            self.assertNotIn("PR still open", "\n".join(self.z.landed_blockers(conn, s())))
        with patch.object(self.z, "pr_snapshot", return_value=None):
            self.assertIn("PR state unknown", "\n".join(self.z.landed_blockers(conn, s())))

    def test_unpushed_count_ignores_an_upstream_that_is_the_base_branch(self):
        conn = self.session()
        work, env = self.git_worktree()
        conn.execute("UPDATE sessions SET worktree=?", (str(work),))
        conn.commit()
        subprocess.run(["git", "-C", str(work), "branch", "-u", "origin/feature/qa"], check=True, env=env)
        s = conn.execute("SELECT * FROM sessions").fetchone()
        self.assertEqual(self.z.landed_blockers(conn, s), [])
        subprocess.run(["git", "-C", str(work), "checkout", "-qb", "feature/unpushed"], check=True, env=env)
        (work / "n.txt").write_text("n")
        subprocess.run(["git", "-C", str(work), "add", "."], check=True, env=env)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "n"], check=True, env=env)
        subprocess.run(["git", "-C", str(work), "branch", "-u", "origin/feature/qa"], check=True, env=env)  # upstream = another branch
        self.assertIn("1 commit(s) not pushed", "\n".join(self.z.landed_blockers(conn, s)))

    def test_work_merged_into_the_main_checkouts_branch_is_not_unpushed(self):
        conn = self.session()
        main, env = self.git_worktree()  # the repo's own folder, on a branch with an upstream
        wt = self.root / "wt"
        git = lambda path, *a: subprocess.run(["git", "-C", str(path), *a], check=True, env=env, capture_output=True)
        git(main, "worktree", "add", "-q", "-b", "feature/done", str(wt))
        (wt / "n.txt").write_text("n")
        git(wt, "add", ".")
        git(wt, "commit", "-qm", "n")
        conn.execute("UPDATE sessions SET worktree=?", (str(wt),))
        conn.commit()
        s = conn.execute("SELECT * FROM sessions").fetchone()
        (self.root / "config").mkdir(exist_ok=True)
        (self.root / "config" / "projects.json").write_text(json.dumps({"repos": {"scratch": {"path": str(main)}}}))
        self.assertEqual(self.z.main_checkout_heads("scratch", wt), ["refs/heads/feature/qa"])
        self.assertIn("1 commit(s) not pushed", "\n".join(self.z.landed_blockers(conn, s)))
        git(main, "merge", "-q", "feature/done")  # merged into the local-only branch: nothing is lost by removing wt
        self.assertEqual(self.z.landed_blockers(conn, s), [])
        self.assertEqual(self.z.main_checkout_heads("unknown", wt), [])

    def test_close_refuses_unlanded_worktree_but_keep_worktree_closes_the_session(self):
        conn = self.session()
        work, _ = self.git_worktree()
        (work / "dirty.txt").write_text("x")
        conn.execute("UPDATE sessions SET worktree=?", (str(work),))
        conn.commit()
        conn.close()
        with patch.object(self.z, "require_lock"), patch.object(self.z, "project", return_value={}), \
                patch.object(self.z, "close_workspace") as close_ws:
            with self.assertRaises(SystemExit):
                self.z.cmd_close(["1"])
            self.assertIn("not landed", self.output.getvalue())
            self.assertIn("dirty.txt", self.output.getvalue())
            close_ws.assert_not_called()
            self.z.cmd_close(["1", "keep-worktree"])
        self.assertIn("note: S1 is not landed yet", self.output.getvalue())
        close_ws.assert_called_once()
        self.assertTrue(work.exists())

    def test_pr_snapshot_reads_github_only(self):
        gh = json.dumps({"state": "OPEN", "reviews": [{}], "comments": [{}, {}],
                         "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]})
        with patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 0, gh, "")):
            self.assertEqual(self.z.pr_snapshot("https://github.com/o/r/pull/1"),
                             {"state": "open", "reviews": 3, "checks": "failing"})
        with patch.object(self.z, "run", return_value=subprocess.CompletedProcess([], 1, "", "no")):
            self.assertIsNone(self.z.pr_snapshot("https://github.com/o/r/pull/1"))
        with patch.object(self.z, "run") as run:
            for other in ("https://example.com/x", "https://bitbucket.org/ws/repo/pull-requests/7"):
                self.assertIsNone(self.z.pr_snapshot(other))
            run.assert_not_called()

    def test_pr_polling_reports_merge_new_reviews_and_failing_checks_only(self):
        conn = self.session()
        conn.execute("UPDATE sessions SET pr_url='https://github.com/o/r/pull/1', status='finished'")
        conn.commit()
        s = lambda: conn.execute("SELECT * FROM sessions").fetchone()
        alerts = {}
        snaps = iter([{"state": "open", "reviews": 1, "checks": "pending"},    # baseline: silent
                      {"state": "open", "reviews": 1, "checks": "pending"},    # unchanged: silent
                      {"state": "open", "reviews": 3, "checks": "failing"},    # 2 new + failing
                      {"state": "open", "reviews": 3, "checks": "failing"},    # same failure: silent
                      {"state": "merged", "reviews": 3, "checks": "passing"}])
        with patch.object(self.z, "PR_POLL_SECONDS", 0), patch.object(self.z, "pr_snapshot", side_effect=lambda url: next(snaps)):
            self.assertEqual(self.z.pr_events(conn, s(), alerts), [])
            self.assertEqual(self.z.pr_events(conn, s(), alerts), [])
            lines = self.z.pr_events(conn, s(), alerts)
            self.assertIn("2 new review(s)/comment(s)", lines[0])
            self.assertIn("failing checks", lines[1])
            self.assertEqual(self.z.pr_events(conn, s(), alerts), [])
            merged = self.z.pr_events(conn, s(), alerts)
            self.assertIn("was merged", merged[0])
        self.assertEqual(conn.execute("SELECT pr_state FROM sessions").fetchone()[0], "merged")
        self.assertIsNone(conn.execute(self.z.OPEN_PRS).fetchone())  # no longer polled or tracked
        with patch.object(self.z, "PR_POLL_SECONDS", 300), patch.object(self.z, "pr_snapshot", side_effect=AssertionError):
            self.assertEqual(self.z.pr_events(conn, s(), {1: {"pr_at": self.z.time.time()}}), [])  # slow interval

    def test_watch_polls_prs_of_finished_sessions_and_has_tracked_work(self):
        conn = self.session()
        conn.execute("UPDATE sessions SET pr_url='https://github.com/o/r/pull/1', status='finished'")
        conn.commit()
        self.assertTrue(self.z.has_tracked_work())
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "claim_watcher", return_value=True), patch.object(self.z, "PR_POLL_SECONDS", 0), \
                patch.object(self.z, "live_agents", return_value={}), \
                patch.object(self.z, "pr_snapshot", return_value={"state": "closed", "reviews": 0, "checks": None}):
            self.assertTrue(self.z.cmd_watch(["--once", "0.01"]))
        self.assertIn("was closed", self.output.getvalue())
        self.assertFalse(self.z.has_tracked_work())

    def test_watch_runs_the_conductor_each_pass_and_reports_its_wakes(self):
        conn = self.session()
        conn.execute("UPDATE sessions SET pr_url='https://github.com/o/r/pull/1', status='finished'")
        conn.commit()
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "claim_watcher", return_value=True), patch.object(self.z, "PR_POLL_SECONDS", 0), \
                patch.object(self.z, "live_agents", return_value={}), \
                patch.object(self.z, "pr_snapshot", return_value={"state": "open", "reviews": 0, "checks": None}), \
                patch.object(self.z.hwconductor, "tick", return_value=["S9 conductor wake"]) as tick:
            self.assertTrue(self.z.cmd_watch(["--once", "0.01"]))
        self.assertIn("S9 conductor wake", self.output.getvalue())
        self.assertIs(tick.call_args.args[2], self.z.hw_cli)

    def test_hw_cli_runs_the_real_bin_hw_and_returns_code_and_output(self):
        done = subprocess.CompletedProcess([], 4, "out ", "err")
        with patch.object(self.z, "run", return_value=done) as run:
            self.assertEqual(self.z.hw_cli(["lock", "status"]), (4, "out err"))
        self.assertEqual(run.call_args.args[0][1:], [str(self.z.HW_HOME / "bin" / "hw"), "lock", "status"])

    # ---- cleanup at close

    def git(self, path, *args):
        return subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              check=True, capture_output=True, text=True).stdout

    def repo_with_worktree(self):
        repo = self.root / "repo"
        repo.mkdir()
        self.git(repo, "init", "-b", "main")
        (repo / "a.txt").write_text("a")
        self.git(repo, "add", "."), self.git(repo, "commit", "-m", "init")
        remote = self.root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        self.git(repo, "remote", "add", "origin", str(remote)), self.git(repo, "push", "-q", "origin", "main")
        wt = self.root / "wt"
        self.git(repo, "worktree", "add", "-b", "feature/x", str(wt))
        return repo, wt

    def closing(self, conn, repo, *args, in_place=False):
        proj = {"path": str(repo), **({"worktrees": False} if in_place else {})}
        with patch.object(self.z, "db", return_value=conn), patch.object(self.z, "require_lock"), \
                patch.object(self.z, "project", return_value=proj), patch.object(self.z, "close_workspace"), \
                patch.object(self.z, "read_screen"):
            self.z.cmd_close(["S1", *args])

    def worktree_session(self, repo, wt, scratch=None):
        conn = self.session()
        conn.execute("UPDATE sessions SET repo='r', worktree=?, scratch=? WHERE id=1", (str(wt), scratch))
        conn.commit()
        return conn

    def status(self, conn):
        return conn.execute("SELECT status FROM sessions WHERE id=1").fetchone()[0]

    def test_close_removes_a_landed_worktree_even_without_an_upstream(self):
        repo, wt = self.repo_with_worktree()  # branch is in origin/main, no upstream of its own
        conn = self.worktree_session(repo, wt)
        self.closing(conn, repo)
        self.assertFalse(wt.exists())
        self.assertEqual(self.status(conn), "closed")
        self.assertIn("removed worktree", self.output.getvalue())

    def test_close_keeps_a_worktree_with_unpushed_commits(self):
        repo, wt = self.repo_with_worktree()
        (wt / "b.txt").write_text("b")
        self.git(wt, "add", "."), self.git(wt, "commit", "-m", "work")
        conn = self.worktree_session(repo, wt)
        with self.assertRaises(SystemExit):
            self.closing(conn, repo)
        self.assertIn("1 commit(s) not pushed", self.output.getvalue())
        self.assertTrue(wt.exists())
        self.assertEqual(self.status(conn), "running")
        self.closing(conn, repo, "keep-worktree")
        self.assertTrue(wt.exists())
        self.assertEqual(self.status(conn), "closed")

    def scratch_session(self, name="T1-qa"):
        folder = self.root / "folder"
        scratch = folder / name
        scratch.mkdir(parents=True)
        conn = self.session()
        conn.execute("UPDATE sessions SET repo='docs', worktree=?, scratch=? WHERE id=1", (str(folder), str(scratch)))
        conn.commit()
        return folder, scratch, conn

    def test_close_removes_scratch_but_not_the_project_folder(self):
        folder, scratch, conn = self.scratch_session()
        (scratch / "clone").mkdir()
        (scratch / "clone" / "big.bin").write_text("x")
        (folder / "keep.md").write_text("docs")
        self.closing(conn, folder, in_place=True)
        self.assertFalse(scratch.exists())
        self.assertTrue((folder / "keep.md").exists())
        self.assertEqual(self.status(conn), "closed")

    def test_close_keeps_scratch_clone_with_unpushed_commits(self):
        folder, scratch, conn = self.scratch_session()
        clone = scratch / "pr-1"
        self.git(scratch, "init", "-b", "main", str(clone))
        (clone / "f").write_text("f")
        self.git(clone, "add", "."), self.git(clone, "commit", "-m", "mine")
        self.closing(conn, folder, in_place=True)
        self.assertTrue(clone.exists())
        self.assertIn("pr-1: commits not on any remote", self.output.getvalue())
        self.assertEqual(self.status(conn), "closed")

    def test_scratch_outside_its_project_or_oddly_named_is_never_deleted(self):
        folder, scratch, conn = self.scratch_session(name="notes")
        self.closing(conn, folder, in_place=True)
        self.assertTrue(scratch.exists())
        conn.execute("UPDATE sessions SET scratch=? WHERE id=1", (str(self.root),))
        self.assertIsNone(self.z.own_scratch(conn.execute("SELECT * FROM sessions").fetchone()))

    def watch_once(self, *args):
        with patch.object(self.z, "require_lock"), patch.object(self.z, "holds_lock", return_value=True), \
                patch.object(self.z, "live_agents", return_value={}):
            self.z.cmd_watch(["--once", *args])

    def task(self, conn, status="active", title="QA"):
        cur = conn.execute("INSERT INTO tasks(title,status,created_at,updated_at) VALUES (?,?,'2026-10-26 12:00','2026-10-26 12:00')",
                           (title, status))
        conn.commit()
        return cur.lastrowid

    def question(self, conn, task_id=1, status="open"):
        cur = conn.execute("INSERT INTO questions(task_id,question,status,asked_at) VALUES (?,'Ship it?',?,'2026-10-26 12:00')",
                           (task_id, status))
        conn.commit()
        return cur.lastrowid

    def test_herdr_agents_tell_unreachable_from_none_running(self):
        listing = lambda agents: subprocess.CompletedProcess([], 0, json.dumps({"result": {"agents": agents}}), "")
        with patch.object(self.z, "run", return_value=listing([{"pane_id": "w1:p1", "agent_status": "idle"}])):
            self.assertEqual(self.z.herdr_agents(), {"w1:p1": {"pane_id": "w1:p1", "agent_status": "idle"}})
        with patch.object(self.z, "run", return_value=listing([])):
            self.assertEqual(self.z.herdr_agents(), {})
        for unreachable in (subprocess.CompletedProcess([], 1, "", "server_unavailable"),
                            subprocess.CompletedProcess([], 0, "not json", ""),
                            subprocess.CompletedProcess([], 0, "{}", ""), FileNotFoundError("herdr")):
            with self.subTest(unreachable=unreachable):
                effect = {"side_effect": unreachable} if isinstance(unreachable, Exception) else {"return_value": unreachable}
                with patch.object(self.z, "run", **effect):
                    self.assertIsNone(self.z.herdr_agents())
                    self.assertEqual(self.z.live_agents(), {})


if __name__ == "__main__":
    unittest.main()
