"""Wake and hold on OpenCode, end to end: the real `bin/hw` with its long-lived watcher (`watch --daemon`, started by
`start` and `spawn`) against the fake herdr and the fake OpenCode server and TUI (tests/fakes/). Claude Code wakes its
coordinator through Stop hooks instead (tests/test_hooks.py).

| Claim                                                                  | Test                                                              |
|------------------------------------------------------------------------|-------------------------------------------------------------------|
| An idle coordinator is woken through its server; the receipt is kept   | Wake.test_an_idle_coordinator_is_woken_through_its_server_and_the |
|                                                                        | _receipt_is_kept                                                  |
| When the server refuses, the prompt is typed into its pane instead     | Wake.test_when_the_server_refuses_the_wake_is_typed_into_the_pane |
| An event left un-acked is shown again, three times at most             | Wake.test_an_event_left_unacked_wakes_again_three_times_at_most   |
| An event acked in the turn its wake started is not shown again         | Wake.test_an_event_acked_in_the_turn_its_wake_started_is_not_shown|
|                                                                        | _again                                                            |
| A wake typed into the pane is shown again after five minutes           | Wake.test_a_wake_typed_into_the_pane_is_shown_again_after_five_   |
|                                                                        | minutes                                                           |
| One watcher per profile                                                | Wake.test_one_watcher_per_profile                                 |
| The watcher stops when its coordinator loses the lock, and says so     | Wake.test_the_watcher_stops_when_its_coordinator_loses_the_lock   |
| The watcher ends with the coordinator's server and types nothing       | Wake.test_the_watcher_ends_with_the_coordinators_server           |
| It runs only for an OpenCode coordinator                               | Wake.test_it_runs_only_for_an_opencode_coordinator                |
| A turn that ends with work tracked leaves the watcher running          | Hold.test_a_turn_that_ends_with_work_tracked_leaves_the_watcher_  |
|                                                                        | running                                                           |
| A watcher that died is started again by `start` and by `spawn`         | Hold.test_a_watcher_that_died_is_started_again_by_start_and_spawn |
"""
import signal
import unittest

from _e2e import LoopCase, ask

BRANCH = "task/demo"
HEADING = "Watcher events for Herdwright. Handle each one, then retire it with `bin/hw ack E<id>`:"


class DaemonCase(LoopCase):
    """An OpenCode coordinator whose `start` has run: it holds the lock, its long-lived watcher runs, and it is idle."""
    RUNTIME = "opencode"

    def setUp(self):
        super().setUp()
        self.w.daemon = True  # these tests are about the watcher `start` leaves running
        self.started = self.w.start().stdout
        self.w.rest()

    def prompts(self, name="hw"):
        """What the coordinator was prompted with, by its server or through herdr."""
        return self.w.agent(name)["prompts"]

    def woken(self, count=1):
        """Wait until the watcher has kept `count` receipts in the wake queue; returns the queue's events."""
        self.w.wait_for(f"{count} wake receipt(s)", lambda: sum("wake" in e for e in self.w.wakes()) >= count
                        and len(self.prompts()) >= count)
        return self.w.wakes()

    def log(self):
        return (self.w.profile / ".cache" / "watch.log").read_text()


class Wake(DaemonCase):
    def test_an_idle_coordinator_is_woken_through_its_server_and_the_receipt_is_kept(self):
        self.assertRegex(self.started, r"(?m)^watcher: was NOT RUNNING; started now, pid \d+$")
        self.assertEqual(self.prompts(), [])
        self.w.started(BRANCH, turns=[ask("Red or blue?")])
        event, = self.woken()
        prompt, = self.prompts()  # one new turn of the coordinator, started from outside it
        self.assertEqual(prompt.splitlines()[:2], [HEADING, "[E1] S1 T1 build-t1 · status file: HW:QUESTION"])
        self.assertIn("Q1: Red or blue?", prompt)
        self.assertEqual(event["wake"]["via"], "api")
        admitted = [m["id"] for m in self.w.server_messages("hw") if m["type"] == "user"]
        self.assertEqual([event["wake"]["message"]], admitted)  # the receipt is the id the server gave the admitted prompt
        self.assertEqual(self.w.herdr_calls("agent", "prompt"), [])  # nothing was typed into its screen
        self.assertIn(f'woke the coordinator: {{"via": "api", "message": "{admitted[0]}"', self.log())
        self.assertIn("[E1] ", self.w.hw("start").stdout)  # still listed at a session start until it is acknowledged
        self.w.ack_all()
        self.w.hw("prompt", "S1", "Blue.")  # the worker ends another turn with nothing new: no second wake
        self.w.watch_state_changes(3)
        self.assertEqual((len(self.prompts()), len(self.w.wakes())), (1, 1))

    def test_when_the_server_refuses_the_wake_is_typed_into_the_pane(self):
        (self.w.herdr / "opencode-down").write_text(self.w.me["HERDR_PANE_ID"])  # the coordinator's server takes no prompt
        self.w.started(BRANCH, turns=[ask("Red or blue?")])
        event, = self.woken()
        self.assertEqual(event["wake"]["via"], "herdr")
        self.assertIn("HTTPError: HTTP Error 500", event["wake"]["api_error"])
        typed, = self.w.herdr_calls("agent", "prompt")
        self.assertEqual(typed[2], self.w.me["HERDR_PANE_ID"])
        self.assertEqual(self.prompts(), [typed[3]])
        self.assertIn("Q1: Red or blue?", typed[3])
        self.assertEqual([m for m in self.w.server_messages("hw") if m["type"] == "user"], [])

    def test_a_server_that_breaks_off_its_answer_does_not_end_the_watcher(self):
        pid = self.w.watcher_pid()
        (self.w.herdr / "opencode-cut").write_text(self.w.me["HERDR_PANE_ID"])
        self.w.started(BRANCH, turns=[ask("Red or blue?")])
        event, = self.woken()
        self.assertEqual(event["wake"]["via"], "herdr")
        self.assertIn("IncompleteRead", event["wake"]["api_error"])
        self.w.watch_state_changes(2)
        self.assertEqual(self.w.watcher_pid(), pid)

    def test_a_watcher_killed_during_the_wake_does_not_have_its_events_raised_again(self):
        """What a look has seen is saved before the coordinator is woken: the wake is a call that can take long, and a
        watcher that dies in it must not leave its successor to read the same report as new."""
        held = self.w.herdr / "opencode-held"
        held.write_text(self.w.me["HERDR_PANE_ID"])
        self.w.started(BRANCH, turns=[ask("Red or blue?")])
        self.w.wait_for("the wake to reach the server", held.with_name("opencode-held.reached").exists)
        self.w.stop_watcher(signal.SIGKILL)
        held.unlink()
        self.assertIn("[E1] ", self.w.hw("start").stdout)  # the next watcher starts, and the event is still listed
        self.w.watch_state_changes(3)
        self.assertEqual([e["id"] for e in self.w.wakes()], [1])
        self.assertEqual(self.w.rows("SELECT COUNT(*) AS n FROM questions"), [{"n": 1}])

    def test_an_event_left_unacked_wakes_again_three_times_at_most(self):
        self.w.started(BRANCH, turns=[ask("Red or blue?")])
        self.woken()
        self.w.age_wakes()  # however long the turn that took the wake in runs
        self.w.watch_state_changes(2)
        self.assertEqual(len(self.prompts()), 1)  # not again while that turn runs
        for shown in (2, 3):
            self.w.rest()  # the turn ends without an ack
            self.w.wait_for(f"wake {shown}", lambda: len(self.prompts()) == shown)
            self.assertIn("[E1] (again, un-acked since ", self.prompts()[-1])
        self.w.rest()
        self.w.watch_state_changes(2)
        self.assertEqual(len(self.prompts()), 3)  # a forgotten ack cannot wake the coordinator for ever
        self.assertIn("[E1] ", self.w.hw("start").stdout)  # `start` still lists it

    def test_an_event_acked_in_the_turn_its_wake_started_is_not_shown_again(self):
        """T121: the turn ends after the ack, however long it ran; the event is not shown again."""
        self.w.started(BRANCH, turns=[ask("Red or blue?")])
        self.woken()
        self.w.age_wakes()
        self.w.ack_all()
        self.w.rest()
        self.w.watch_state_changes(3)
        self.assertEqual(len(self.prompts()), 1)

    def test_a_wake_typed_into_the_pane_is_shown_again_after_five_minutes(self):
        """It has no message in the session to follow, so the watcher keeps time for it."""
        (self.w.herdr / "opencode-down").write_text(self.w.me["HERDR_PANE_ID"])
        self.w.started(BRANCH, turns=[ask("Red or blue?")])
        self.woken()
        self.w.rest()
        self.w.watch_state_changes(2)
        self.assertEqual(len(self.prompts()), 1)
        self.w.age_wakes()  # five minutes pass without an ack
        self.w.wait_for("wake 2", lambda: len(self.prompts()) == 2)
        self.assertIn("[E1] (again, un-acked since ", self.prompts()[-1])

    def test_one_watcher_per_profile(self):
        pid = self.w.watcher_pid()
        self.assertIn(f"watcher: alive, pid {pid}", self.w.hw("start").stdout)
        for form in ("--daemon", "--once"):
            r = self.w.hw("watch", form, check=False)
            self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
            self.assertIn(f"WATCH_ALREADY_RUNNING pid {pid}", r.stdout)
        self.w.started(BRANCH)  # `spawn` starts none either
        self.assertEqual(self.w.watcher_pid(), pid)

    def test_the_watcher_stops_when_its_coordinator_loses_the_lock(self):
        other = self.w.coordinator("hw-b")
        self.w.script()
        self.w.hw("lock", "acquire", "--take-over", coordinator=other)
        self.w.wait_for("the watcher to stop", lambda: self.w.watcher_pid() is None)
        told, = self.prompts()  # the session that was Herdwright is told, in its own session
        self.assertIn("LOCK LOST: another session took over as Herdwright", told)
        self.assertEqual(self.prompts("hw-b"), [])
        self.assertEqual(self.w.herdr_calls("agent", "prompt"), [])

    def test_the_watcher_ends_with_the_coordinators_server(self):
        self.w.stop_server("hw")
        self.w.wait_for("the watcher to end", lambda: self.w.watcher_pid() is None)
        self.assertIn("COORDINATOR GONE", self.log())
        self.assertEqual(self.w.herdr_calls("agent", "prompt"), [])  # nothing is typed into a pane whose session is gone

    def test_it_runs_only_for_an_opencode_coordinator(self):
        self.w.stop_watcher()
        env = {k: v for k, v in self.w.me.items() if k != "HW_COORDINATOR"}  # a session `bin/hw coordinator` did not open
        r = self.w.hw("watch", "--daemon", coordinator=env, check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("`watch --daemon` wakes an OpenCode coordinator through its server", r.stderr)
        self.assertIsNone(self.w.watcher_pid())


class Hold(DaemonCase):
    """OpenCode cannot refuse a turn's end, and Claude Code's case of work tracked with no watcher armed arises when a
    turn ends: here the watcher does not end with a turn, so that case does not arise."""

    def test_a_turn_that_ends_with_work_tracked_leaves_the_watcher_running(self):
        pid = self.w.watcher_pid()
        s = self.w.started(BRANCH, turns=[[{"pause": True}, *ask("Red or blue?")]])
        self.w.rest()  # the coordinator's turn ends while its worker is busy
        self.w.watch_state_changes(3)
        self.assertEqual((self.w.watcher_pid(), self.prompts()), (pid, []))
        self.w.advance(s["agent"])  # later the worker asks: the same watcher wakes the coordinator
        self.woken()
        self.assertEqual(self.w.watcher_pid(), pid)
        self.assertIn("Q1: Red or blue?", self.prompts()[0])

    def test_a_watcher_that_died_is_started_again_by_start_and_spawn(self):
        self.w.stop_watcher(signal.SIGKILL)
        out = self.w.hw("start").stdout
        self.assertRegex(out, r"(?m)^watcher: was NOT RUNNING; started now, pid \d+$")
        first = self.w.watcher_pid()
        self.assertIsNotNone(first)
        self.w.stop_watcher(signal.SIGKILL)
        self.w.started(BRANCH, turns=[ask("Red or blue?")])  # work is never tracked without a watcher
        self.assertNotIn(self.w.watcher_pid(), (None, first))
        self.woken()


if __name__ == "__main__":
    unittest.main()
