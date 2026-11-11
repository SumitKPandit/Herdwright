"""The core loop, end to end: the real `bin/zed` (and `zed-employee`, `zed-guard`) against a fake herdr and a fake
agent CLI (tests/fakes/), in a throwaway profile and a throwaway git repo (tests/_e2e.py).

One named test per claim:

| Claim                                                             | Test                                                                 |
|-------------------------------------------------------------------|----------------------------------------------------------------------|
| A task is recorded                                                | Record.test_a_task_is_recorded                                       |
| A brief is required                                               | Brief.test_no_worker_starts_without_a_brief                          |
| A brief with unfilled spots is refused                            | Brief.test_no_worker_starts_on_a_brief_with_unfilled_spots           |
| A brief without a plan is refused                                 | Brief.test_no_worker_starts_on_a_brief_without_a_plan                |
| A worker starts in its own worktree and is handed its brief once  | Spawn.test_a_worker_starts_in_its_own_worktree_with_its_brief        |
| The folder-trust dialog of a new worktree does not stop the start | Spawn.test_the_trust_dialog_of_a_new_worktree_is_accepted            |
| The watcher reports a question once, until acknowledged           | Watch.test_a_question_is_reported_once_until_acknowledged            |
| ... also one a new worker has only on its screen                  | Watch.test_a_question_on_the_screen_of_a_new_worker_is_reported_once |
| A new worker that is idle without a report raises nothing         | Watch.test_a_new_worker_idle_without_a_report_raises_nothing         |
| ... and is nudged once it is no longer new                        | Watch.test_a_worker_idle_without_a_report_is_nudged_once_it_is_no_   |
|                                                                   | longer_new                                                           |
| The watcher reports a block once                                  | Watch.test_a_block_is_reported_once                                  |
| The watcher reports a DONE once and closes the finished session   | Watch.test_a_done_is_reported_once_and_the_session_closed            |
| The watcher reports a permission prompt once                      | Watch.test_a_permission_prompt_is_reported_once                      |
| The watcher reports a vanished pane once                          | Watch.test_a_vanished_pane_is_reported_once                          |
| A DONE without QA is flagged                                      | Watch.test_a_done_without_qa_is_flagged                              |
| An answer is relayed verbatim and recorded; a working worker is   | Relay.test_an_answer_is_relayed_verbatim_and_recorded_and_a_working_ |
| not interrupted                                                   | worker_is_not_interrupted                                            |
| Closing refuses unlanded work                                     | Close.test_closing_refuses_unlanded_work                             |
| Closing removes a landed worktree                                 | Close.test_closing_removes_a_landed_worktree                         |
| A second coordinator cannot take the lock silently                | Lock.test_a_second_coordinator_cannot_take_the_lock_silently         |
| A coordinator that lost the lock stops watching                   | Lock.test_a_watcher_stops_when_its_coordinator_loses_the_lock        |
| The lock of a coordinator whose pane is gone is cleared           | Lock.test_the_lock_of_a_coordinator_whose_pane_is_gone_is_cleared    |
| A merge is refused without clean review and QA by non-authors     | Merge.test_a_merge_waits_for_a_clean_review_and_qa                   |
| A reviewer that changed the code gives no verdict                 | Merge.test_a_reviewer_that_changed_the_code_gives_no_verdict         |
"""
import subprocess
import unittest

from _e2e import BASE, REPO, RUN_ZED, LoopCase, ask, done

BRANCH = "task/demo"
FINISH = {"run": ["{bin}/zed-employee", "finish"]}
TRUST_DIALOG = "Do you trust the files in this folder?\n❯ 1. Yes, I trust this folder\n  2. No, exit"
PERMISSION_PROMPT = "Bash(rm -rf build)\nDo you want to proceed?\n❯ 1. Yes\n  2. No"
PLANNED = [{"plan": ["Do the work"]}, {"tick": "all"}]  # a plan with every step ticked but the one `finish` ticks


class Record(LoopCase):
    def test_a_task_is_recorded(self):
        tid = self.w.task("Add a greeting", description="Say hello on the home page")
        self.assertEqual(self.w.rows("SELECT title, status, description FROM tasks WHERE id=?", tid),
                         [{"title": "Add a greeting", "status": "intake", "description": "Say hello on the home page"}])
        self.assertEqual(self.w.events("created"), ["Add a greeting"])
        self.assertIn("Add a greeting", self.w.zed("task", "list").stdout)
        self.assertIn("Add a greeting", self.w.zed("task", "show", f"T{tid}").stdout)
        self.assertIn("Add a greeting", (self.w.profile / "STATUS.md").read_text())


class Brief(LoopCase):
    def refused(self, brief, reason):
        r = self.w.spawn(self.tid, BRANCH, brief=brief, check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(reason, r.stderr)
        self.assertEqual(self.w.herdr_calls("workspace", "create"), [])
        self.assertEqual(self.w.herdr_calls("agent", "start"), [])
        self.assertEqual(self.w.rows("SELECT id FROM sessions"), [])
        self.assertFalse(self.w.worktrees.exists())

    def setUp(self):
        super().setUp()
        self.tid = self.w.task()

    def test_no_worker_starts_without_a_brief(self):
        self.refused(self.w.profile / "briefs" / "missing.md", "brief not found")
        r = self.w.zed("spawn", f"T{self.tid}", f"repo={REPO}", f"base={BASE}", f"branch={BRANCH}", check=False)
        self.assertIn("spawn needs brief=", r.stderr)
        self.assertEqual(self.w.rows("SELECT id FROM sessions"), [])

    def test_no_worker_starts_on_a_brief_with_unfilled_spots(self):
        self.refused(self.w.brief(self.tid, BRANCH, fill=None), "unfilled placeholder")

    def test_no_worker_starts_on_a_brief_without_a_plan(self):
        brief = self.w.brief(self.tid, BRANCH)
        brief.write_text(brief.read_text().replace("## Plan", "## Steps"))
        self.refused(brief, "no worker starts without a plan")


class Spawn(LoopCase):
    def test_a_worker_starts_in_its_own_worktree_with_its_brief(self):
        s = self.w.started(BRANCH)
        worktree = self.w.worktrees / REPO / "demo"
        self.assertEqual(s["worktree"], str(worktree))
        self.assertEqual(self.w.git("branch", "--show-current", cwd=worktree).stdout.strip(), BRANCH)
        self.assertEqual(self.w.git("branch", "--show-current").stdout.strip(), BASE)  # the user's checkout is left alone
        self.assertIn(str(worktree), self.w.git("worktree", "list").stdout)

        row = self.w.session(s["session"])
        self.assertEqual((row["status"], row["hat"], row["worktree"], row["branch"], row["pane_id"]),
                         ("running", "build", str(worktree), BRANCH, s["pane"]))
        self.assertEqual(self.w.rows("SELECT status FROM tasks WHERE id=?", s["tid"]), [{"status": "active"}])
        self.assertEqual(len(self.w.events("spawn")), 1)

        create = self.w.herdr_calls("workspace", "create")[0]
        self.assertEqual(create[create.index("--cwd") + 1], str(worktree))  # the agent runs in the worktree
        agent = self.w.agent(s["agent"])
        self.assertEqual(len(agent["prompts"]), 1)  # handed over once
        brief = self.w.profile / "briefs" / f"T{s['tid']}-{REPO}.md"
        self.assertEqual(agent["facts"]["brief"], str(brief))
        self.assertEqual(agent["facts"]["status_file"], str(self.w.profile / ".cache" / "status" / f"{s['session']}.log"))
        self.assertEqual(agent["facts"]["rules"], agent["launch"]["rules"])  # the rules it is told to read are the ones it runs under
        with open(agent["launch"]["rules"]) as rules:
            self.assertIn("Hat: build", rules.read())

        # the worktree is guarded: its own branch takes a commit, a protected one does not
        (worktree / "a.txt").write_text("a\n")
        self.w.git("add", "-A", cwd=worktree)
        self.w.git("commit", "-q", "-m", "Add a", cwd=worktree)
        self.w.git("checkout", "-q", "-b", "releases/1", cwd=worktree)
        (worktree / "b.txt").write_text("b\n")
        self.w.git("add", "-A", cwd=worktree)
        r = self.w.git("commit", "-q", "-m", "Add b", cwd=worktree, check=False)
        self.assertIn("refusing to commit on protected branch", r.stderr)

    def test_the_trust_dialog_of_a_new_worktree_is_accepted(self):
        s = self.w.started(BRANCH, start={"dialog": TRUST_DIALOG})
        agent = self.w.agent(s["agent"])
        self.assertEqual(agent["keys"], [["down", "enter"]])
        self.assertEqual(len(agent["prompts"]), 1)
        self.assertEqual(self.w.session(s["session"])["status"], "running")


class Watch(LoopCase):
    def reported_once(self, first, *needles):
        """`first` is what the watcher printed for a new event. Checks that it is E1 and holds `needles`, that a later
        watcher shows the same event again and raises nothing new, and that it is gone once acknowledged."""
        for needle in ("[E1] ", *needles):
            self.assertIn(needle, first)
        self.assertNotIn("[E2]", first)
        again = self.w.watch_ticks(2)
        self.assertIn("[E1] (again, un-acked since", again)
        self.assertNotIn("[E2]", again)
        self.assertIn("acked 1; 0 still un-acked", self.w.zed("ack", "E1").stdout)
        self.assertNotIn("[E", self.w.watch_ticks(2))
        self.assertEqual(len(self.w.wakes()), 1)

    def test_a_question_is_reported_once_until_acknowledged(self):
        s = self.w.started(BRANCH, turns=[ask("Red or blue? I recommend red.", "Round or square corners?")])
        first = self.w.watch_once().stdout
        self.assertIn("[E1] ", self.w.watch_once().stdout)  # `watch --once` returns with it again while it is un-acked
        self.assertIn("[E1] ", self.w.start().stdout)  # and a coordinator session is told at its start
        self.reported_once(first, "ZED:QUESTION", "Q1: Red or blue? I recommend red.", "Q2: Round or square corners?")
        self.assertNotIn("[E1]", self.w.start().stdout)
        self.assertEqual(self.w.rows("SELECT question, status, session_id FROM questions ORDER BY id"),
                         [{"question": "Red or blue? I recommend red.", "status": "open", "session_id": 1},
                          {"question": "Round or square corners?", "status": "open", "session_id": 1}])
        self.assertEqual(self.w.session(s["session"])["status"], "waiting_user")
        self.assertEqual(self.w.rows("SELECT status FROM tasks"), [{"status": "waiting_user"}])
        self.assertIn("Q1 T1 S1", self.w.zed("q", "list").stdout)

    def test_a_question_on_the_screen_of_a_new_worker_is_reported_once(self):
        """A worker that ends its turn within three minutes of its start, with a ZED block on its screen and no
        status-file line."""
        self.w.watch_ticks(1)  # a watcher has looked before the worker starts, as in any running coordinator
        self.w.started(BRANCH, turns=[[{"block": "QUESTION", "text": "1. Red or blue?"}]])
        self.reported_once(self.w.watch_once().stdout, "ZED:QUESTION", "Q1: Red or blue?")
        self.w.age_sessions()  # the three minutes pass
        self.assertNotIn("[E", self.w.watch_ticks(2))
        self.assertEqual(len(self.w.rows("SELECT id FROM questions")), 1)

    def new_idle_worker(self):
        """A worker under three minutes old that is idle with nothing to report, as one that has not started on its
        brief yet is; a watcher was already running when it started, and has seen it idle."""
        self.w.watch_ticks(1)
        s = self.w.started(BRANCH)
        self.assertNotIn("[E", self.w.watch_ticks(2))
        return s

    def test_a_new_worker_idle_without_a_report_raises_nothing(self):
        s = self.new_idle_worker()
        self.w.age_lapses()  # idle for longer than the grace period of a lapse, and still new
        self.assertNotIn("[E", self.w.watch_ticks(2))
        self.assertEqual(self.w.wakes(), [])
        self.assertEqual(self.w.events("auto"), [])
        self.assertEqual(len(self.w.agent(s["agent"])["prompts"]), 1)  # the brief; no nudge

    def test_a_worker_idle_without_a_report_is_nudged_once_it_is_no_longer_new(self):
        s = self.new_idle_worker()
        self.w.age_sessions()  # the three minutes pass
        self.w.age_lapses()
        self.assertNotIn("[E", self.w.watch_ticks(2))  # a first nudge does not wake the coordinator
        self.assertEqual(self.w.events("auto"), ["S1 idle with no ZED block; sent the standard nudge"])
        self.assertEqual(self.w.agent(s["agent"])["prompts"][1:],
                         ["Continue, or end with ZED:QUESTION / ZED:DONE / ZED:BLOCKED."])

    def test_a_block_is_reported_once(self):
        reason = "No access to the staging database; I need a login."
        s = self.w.started(BRANCH, turns=[[{"block": "BLOCKED", "text": reason}]])  # on its screen only: no status line
        self.reported_once(self.w.watch_once().stdout, "ZED:BLOCKED",
                           "Q1: BLOCKED: No access to the staging database; I need a login.")
        self.assertEqual(len(self.w.rows("SELECT id FROM questions")), 1)
        self.assertEqual(self.w.session(s["session"])["status"], "waiting_user")

    def test_a_done_is_reported_once_and_the_session_closed(self):
        s = self.w.started(BRANCH, turns=[[*PLANNED, FINISH, *done("Added the greeting", qa="ran the page, saw hello")]])
        self.assertEqual(self.w.agent(s["agent"])["results"][0]["rc"], 0, self.w.agent(s["agent"])["results"])
        self.reported_once(self.w.watch_once().stdout, "ZED:DONE · Added the greeting", "QA: ran the page, saw hello")
        self.assertEqual(self.w.events("done"), ["Added the greeting"])
        self.assertEqual(self.w.events("finish"), ["S1 build"])
        self.assertEqual(self.w.session(s["session"])["status"], "closed")  # finished and `finish` ran: closed without a turn
        self.assertIsNone(self.w.agent(s["agent"]))
        self.assertEqual(len(self.w.herdr_calls("workspace", "close")), 1)
        self.assertTrue((self.w.worktrees / REPO / "demo").is_dir())  # kept: review and QA read it

    def test_a_permission_prompt_is_reported_once(self):
        s = self.w.started(BRANCH, turns=[[{"pause": True}, {"dialog": PERMISSION_PROMPT}]])
        self.assertNotIn("[E", self.w.watch_ticks(2))  # working: nothing to report
        self.w.advance(s["agent"])
        self.reported_once(self.w.watch_once().stdout, "S1 T1 build-t1 → blocked", "Bash(rm -rf build)", "Do you want to proceed?")
        self.assertEqual(self.w.agent(s["agent"])["keys"], [])  # nothing was answered for the worker

    def test_a_vanished_pane_is_reported_once(self):
        s = self.w.started(BRANCH, turns=[[{"pause": True}]])
        self.assertNotIn("[E", self.w.watch_ticks(2))
        self.w.kill_pane(s["agent"])
        self.reported_once(self.w.watch_once().stdout, "S1 T1 build-t1 → gone")
        self.assertIn("agent=GONE", self.w.zed("session", "list").stdout)

    def test_a_done_without_qa_is_flagged(self):
        pr = "https://github.com/acme/demo/pull/7"
        s = self.w.started(BRANCH, turns=[[*PLANNED, FINISH, *done("Added the greeting", qa="none", pr=pr)]])
        out = self.w.watch_once().stdout
        self.assertIn("PR with no real QA in its QA: line", out)
        self.assertIn(f"PR: {pr}", out)
        self.assertEqual(self.w.session(s["session"])["pr_url"], pr)
        self.assertEqual(self.w.rows("SELECT status FROM tasks"), [{"status": "active"}])  # not accepted as a PR in review


class Relay(LoopCase):
    ANSWER = 'Use "blue" — not red; keep $HOME and `ls *` as they are & ask Zoë (50% sure).\nSecond line: 2 > 1.'

    def test_an_answer_is_relayed_verbatim_and_recorded_and_a_working_worker_is_not_interrupted(self):
        s = self.w.started(BRANCH, turns=[ask("Red or blue?"), [{"say": "Blue it is."}, {"pause": True}]])
        self.w.watch_once()
        self.w.zed("q", "answer", "Q1", self.ANSWER)
        self.assertIn("prompted S1", self.w.zed("prompt", s["session"], self.ANSWER).stdout)

        agent = self.w.agent(s["agent"])
        self.assertEqual(agent["prompts"][1], self.ANSWER)  # character for character
        self.assertEqual(len(agent["prompts"]), 2)
        self.assertEqual(agent["status"], "working")
        self.assertEqual(self.w.rows("SELECT answer, status FROM questions"), [{"answer": self.ANSWER, "status": "answered"}])
        self.assertEqual(self.w.events("answer"), [f"Q1: {self.ANSWER}"])
        self.assertEqual(self.w.events("prompt"), ["S1 prompted by Zed"])
        self.assertEqual(self.w.session(s["session"])["status"], "running")
        self.assertEqual(self.w.rows("SELECT status FROM tasks"), [{"status": "active"}])
        self.assertEqual(self.w.zed("q", "list").stdout, "")

        # the worker is busy with the answer now: a second prompt would derail it, so it is refused unless meant to
        r = self.w.zed("prompt", s["session"], "Stop and do something else", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refused: S1 the session is working", r.stderr)
        self.assertEqual((len(self.w.agent(s["agent"])["prompts"]), self.w.agent(s["agent"])["queued"]), (2, []))
        self.w.zed("prompt", s["session"], "--redirect", "Stop and do something else")
        self.assertEqual(self.w.agent(s["agent"])["queued"], ["Stop and do something else"])
        self.assertEqual(self.w.events("prompt"), ["S1 prompted by Zed", "S1 prompted by Zed (redirect)"])


class Close(LoopCase):
    def test_closing_refuses_unlanded_work(self):
        s = self.w.started(BRANCH, turns=[[{"plan": ["Do the work"]}, {"write": "greeting.txt", "text": "hello\n"},
                                           *ask("Red or blue?")]])
        worktree = self.w.worktrees / REPO / "demo"
        self.w.watch_once()

        def refused(*reasons):
            r = self.w.zed("close", s["session"], check=False)
            self.assertNotEqual(r.returncode, 0)
            for reason in ("is not landed, so its worktree stays", *reasons):
                self.assertIn(reason, r.stderr)
            self.assertEqual((worktree / "greeting.txt").read_text(), "hello\n")
            self.assertNotEqual(self.w.session(s["session"])["status"], "closed")
            self.assertEqual(self.w.herdr_calls("workspace", "close"), [])  # the worker's pane stays too

        refused("1 uncommitted file(s): greeting.txt", "plan has 2 of 2 steps left", "1 open question(s) for the user")
        self.assertIn("1 uncommitted file(s)", self.w.zed("landed", s["session"], check=False).stdout)
        self.w.git("add", "-A", cwd=worktree)
        self.w.git("commit", "-q", "-m", "Add the greeting", cwd=worktree)
        refused("1 commit(s) not pushed")

    def test_closing_removes_a_landed_worktree(self):
        s = self.w.started(BRANCH, turns=[[*PLANNED, FINISH]])  # nothing uncommitted, nothing unpushed, plan complete
        worktree = self.w.worktrees / REPO / "demo"
        self.assertTrue(worktree.is_dir())
        self.assertIn("is landed", self.w.zed("landed", s["session"]).stdout)
        out = self.w.zed("close", s["session"]).stdout
        self.assertIn(f"removed worktree {worktree}", out)
        self.assertFalse(worktree.exists())
        self.assertNotIn(str(worktree), self.w.git("worktree", "list").stdout)
        self.assertEqual(self.w.session(s["session"])["status"], "closed")
        self.assertIsNone(self.w.agent(s["agent"]))  # its pane is closed too
        self.assertEqual(len(self.w.events("close")), 1)


class Lock(LoopCase):
    def setUp(self):
        super().setUp()
        self.other = self.w.coordinator("zed-b")

    def holder(self):
        return self.w.zed("lock", "status").stdout

    def test_a_second_coordinator_cannot_take_the_lock_silently(self):
        r = self.w.zed("lock", "acquire", coordinator=self.other, check=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("the lock is held by a live session", r.stderr)
        self.assertIn("NOT THE ACTIVE ZED", self.w.start(coordinator=self.other).stdout)
        for write in (["task", "add", "title=Sneak in"], ["watch", "--once"], ["ack", "--all"], ["log", "-", "a note"]):
            r = self.w.zed(*write, coordinator=self.other, check=False)
            self.assertNotEqual(r.returncode, 0, write)
            self.assertIn("another session is acting as Zed", r.stderr)
        self.assertEqual(self.w.rows("SELECT id FROM tasks"), [])
        self.assertIn('"you_hold_it": true', self.holder())  # the first one still holds it
        self.w.zed("task", "list", coordinator=self.other)  # reading stays possible

        out = self.w.zed("lock", "acquire", "--take-over", coordinator=self.other).stdout  # only when asked to, and said aloud
        self.assertIn("took over the lock from pane", out)
        self.assertIn('"you_hold_it": false', self.holder())
        r = self.w.zed("task", "add", "title=Too late", check=False)
        self.assertIn("another session is acting as Zed", r.stderr)
        self.w.zed("task", "add", "title=Mine now", coordinator=self.other)

    def test_a_watcher_stops_when_its_coordinator_loses_the_lock(self):
        watcher = subprocess.Popen([*RUN_ZED, "watch", "0.05"], env=self.w.env(), cwd=self.w.root,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(watcher.kill)
        self.w.watch_state_changes(1)
        self.w.zed("lock", "acquire", "--take-over", coordinator=self.other)
        out, _ = watcher.communicate(timeout=20)
        self.assertEqual(watcher.returncode, 3)
        self.assertIn("LOCK LOST: another session took over as Zed", out)

    def test_the_lock_of_a_coordinator_whose_pane_is_gone_is_cleared(self):
        self.w.kill_pane("zed")
        out = self.w.zed("lock", "acquire", coordinator=self.other).stdout
        self.assertIn("cleared stale lock", out)
        self.assertIn("acquired the lock", out)


class Merge(LoopCase):
    BUILD = [[*PLANNED, {"write": "greeting.txt", "text": "hello\n"}, {"git": ["add", "-A"]},
              {"git": ["commit", "-q", "-m", "Add the greeting"]}, FINISH, *done("Added the greeting", qa="ran it")]]
    FINDINGS = "../findings.md"  # beside the worktree: a review leaves the code as it found it

    def setUp(self):
        """A task whose build is done and whose build session is closed."""
        super().setUp()
        self.w.script(default={"turns": self.BUILD})
        self.tid = self.w.task()
        self.w.brief(self.tid, BRANCH)
        self.w.spawn(self.tid, BRANCH)
        self.assertIn("ZED:DONE", self.w.watch_once().stdout)
        self.w.ack_all()
        self.worktree = self.w.worktrees / REPO / "demo"

    def gate(self, hat, verdict, before=()):
        """Run a review or QA session on the build's worktree that does `before` and then finishes with `verdict`.
        Returns the result of its `zed-employee finish`."""
        finish = ["{bin}/zed-employee", "finish", "--verdict", verdict, *(["--draft", self.FINDINGS] if verdict == "blockers" else [])]
        self.w.script(default={"turns": [[*PLANNED, *before, {"run": finish},
                                          *done(f"{hat} of the greeting: {verdict}", qa="read and ran the change")]]})
        s = self.w.spawn(self.tid, BRANCH, hat=hat, brief=self.w.brief(self.tid, BRANCH, name=f"{hat}-{verdict}"))
        result = self.w.agent(s["agent"])["results"][-1]
        self.assertIn("ZED:DONE", self.w.watch_once().stdout)
        self.w.ack_all()
        return result

    def refused(self, reason):
        head = self.w.git("rev-parse", "HEAD").stdout
        r = self.w.zed("merge", BRANCH, str(self.w.repo), check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refused: ", r.stderr)
        self.assertIn(reason, r.stderr)
        self.assertEqual(self.w.git("rev-parse", "HEAD").stdout, head)  # nothing was merged

    def test_a_merge_waits_for_a_clean_review_and_qa(self):
        self.refused("no review verdict recorded for this code")
        push = self.w.git("push", "origin", BRANCH, cwd=self.worktree, check=False)  # nor may the work leave the worktree
        self.assertIn("no review verdict recorded", push.stderr)

        findings = [{"write": self.FINDINGS, "text": "greeting.txt:1 greets nobody in particular\n"}]
        self.assertEqual(self.gate("review", "blockers", before=findings)["rc"], 0)
        self.refused("is not clean by a non-author (S2, verdict blockers)")
        self.assertEqual(len(self.w.events("findings")), 1)

        self.assertEqual(self.gate("review", "clean")["rc"], 0)
        self.refused("no qa verdict recorded for this code")

        self.assertEqual(self.gate("qa", "clean")["rc"], 0)
        r = self.w.zed("merge", BRANCH, str(self.w.repo), check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.w.git("rev-list", "--parents", "-n", "1", "HEAD").stdout.split()), 3)  # a merge commit
        self.assertEqual((self.w.repo / "greeting.txt").read_text(), "hello\n")
        self.assertIn("gate passed", self.w.events("merge")[-1])

        # the loop's end: the merged work is landed, so closing removes the worktree
        self.assertIn(f"removed worktree {self.worktree}", self.w.zed("close", "S1").stdout)
        self.assertFalse(self.worktree.exists())

    def test_a_reviewer_that_changed_the_code_gives_no_verdict(self):
        fix = [{"write": "greeting.txt", "text": "hello!\n"}, {"git": ["add", "-A"]}, {"git": ["commit", "-q", "-m", "Fix it myself"]}]
        result = self.gate("review", "clean", before=fix)
        self.assertEqual(result["rc"], 1)
        self.assertIn("whoever changes it is its author", result["out"])
        self.assertEqual(self.w.rows("SELECT verdict FROM sessions WHERE hat='review'"), [{"verdict": None}])
        self.refused("no review verdict recorded for this code")


if __name__ == "__main__":
    unittest.main()
