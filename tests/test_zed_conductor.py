"""The lifecycle conductor: a finished hat session is closed once it has run `finish`. The DB, the spawn path and the git
worktree are real (throwaway profile); only the `bin/zed` child process is a recording fake."""
import sqlite3
import unittest

import _load  # noqa: F401  (puts bin/lib on sys.path)

import zedconductor
from test_zed_assign import AssignCase

CLEAN = (0, "ok")


class Cli:
    """A fake `bin/zed` runner: records every command, answers from `replies` (command word -> (rc, output)).
    Like the real child it is another connection to the DB (it waits 0.1 s for a lock, the real one 10 s), and a
    successful `close` closes the session."""

    def __init__(self, db, **replies):
        self.db, self.calls, self.replies = db, [], replies

    def __call__(self, args):
        self.calls.append(args)
        rc, out = self.replies.get(args[0], CLEAN)
        if rc:
            return rc, out
        try:
            child = sqlite3.connect(self.db, timeout=0.1)
            with child:
                if args[0] == "close":
                    child.execute("UPDATE sessions SET status='closed' WHERE id=?", (int(args[1][1:]),))
            child.close()
        except sqlite3.OperationalError as e:
            return 1, str(e)
        return rc, out

    def words(self):
        return [c[0] for c in self.calls]


class ConductorCase(AssignCase):
    def setUp(self):
        super().setUp()
        self.alerts = {}
        self.clock = [1000.0]

    def cli(self, **replies):
        return Cli(str(self.profile / "db" / "zed.db"), **replies)

    def tick(self, cli):
        return zedconductor.tick(self.conn, self.alerts, cli, clock=lambda: self.clock[0])

    def ran_finish(self, sid):
        self.conn.execute("INSERT INTO events(task_id, ts, kind, text) VALUES (20,'x','finish',?)", (f"S{sid} build",))
        self.conn.commit()


class FinishedSessionTests(ConductorCase):
    def test_a_finished_session_that_ran_finish_is_closed_keeping_its_worktree(self):
        sid = self.spawn("build")
        self.finish(sid)
        self.ran_finish(sid)
        cli = self.cli()
        self.assertEqual(self.tick(cli), [])
        self.assertEqual(cli.calls, [["close", f"S{sid}", "keep-worktree"]])

    def test_a_finished_session_that_has_not_run_finish_waits_then_wakes_once_and_is_never_closed(self):
        sid = self.spawn("build")
        self.finish(sid)
        cli = self.cli()
        self.assertEqual(self.tick(cli), [])
        self.clock[0] += zedconductor.FINISH_WAIT - 1
        self.assertEqual(self.tick(cli), [])
        self.clock[0] += 1
        wake = self.tick(cli)
        self.assertEqual(len(wake), 1)
        self.assertIn("zed-employee finish", wake[0])
        self.assertEqual(self.tick(cli), [])  # once
        self.assertEqual(cli.calls, [])

    def test_the_wait_ends_without_a_wake_when_finish_runs_in_time(self):
        sid = self.spawn("build")
        self.finish(sid)
        cli = self.cli()
        self.tick(cli)
        self.ran_finish(sid)
        self.clock[0] += 500
        self.assertEqual(self.tick(cli), [])
        self.assertEqual(cli.words(), ["close"])

    def test_a_failed_close_wakes_once_per_reason(self):
        sid = self.spawn("build")
        self.finish(sid)
        self.ran_finish(sid)
        cli = self.cli(close=(1, "boom"))
        self.assertEqual(len(self.tick(cli)), 1)
        self.assertEqual(self.tick(cli), [])

    def test_sessions_that_are_running_have_no_hat_or_are_external_are_left_alone(self):
        """Each odd session has a finish event, so only the filter in `finished_sessions` keeps it out."""
        running = self.spawn("build")
        self.ran_finish(running)
        for hat, extra in ((None, ""), ("build", ", external=1")):
            cur = self.conn.execute("INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,status,hat,created_at,updated_at) "
                                    "VALUES (20,'api','main','b','w','odd','finished',?,'x','x')", (hat,))
            self.conn.execute(f"UPDATE sessions SET status='finished'{extra} WHERE id=?", (cur.lastrowid,))
            self.ran_finish(cur.lastrowid)
        cli = self.cli()
        self.assertEqual(self.tick(cli), [])
        self.assertEqual(cli.calls, [])


if __name__ == "__main__":
    unittest.main()
