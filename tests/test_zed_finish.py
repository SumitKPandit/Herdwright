"""`zed-employee finish`: how a hat session ends (plan ticked, verdict stored with the code it is about) and the Stop guard."""
import gc
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _load  # noqa: F401

import zedemployee as emp
import zedlib
from test_zed_employee import ProfileCase, run

SESSIONS = """CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY, task_id INTEGER, repo TEXT, base TEXT, branch TEXT,
  worktree TEXT, agent TEXT, pane_id TEXT, pr_url TEXT, pr_state TEXT, status TEXT DEFAULT 'running', created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, title TEXT, status TEXT DEFAULT 'active');
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, task_id INTEGER, ts TEXT, kind TEXT, text TEXT);"""


class AssignDb(ProfileCase):
    """A profile with a task DB and helpers that seed hat sessions straight into it."""

    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(os.environ))
        for k in ("ZED_SESSION", "ZED_HAT", "HERDR_PANE_ID"):  # a test run inside a worker session must not inherit its identity
            os.environ.pop(k, None)
        gc.collect()  # connections other test modules leaked warn when collected: not inside a captured `finish` run
        self.conn = emp.connect()
        self.addCleanup(self.conn.close)
        self.conn.executescript(SESSIONS)
        zedlib.ensure_session_schema(self.conn)
        self.conn.execute("INSERT INTO tasks(id, title) VALUES (20, 'Role guard'), (21, 'Other')")
        self.conn.commit()
        self.n = 0
        (self.profile / ".cache").mkdir()  # Zed: the session holding the lock, in the pane `zedpane` (herdr says so)
        self.lock = {"pane": "zedpane", "agent_session": "zed-agent", "pid": 4242}
        (self.profile / ".cache" / "zed.lock").write_text(json.dumps(self.lock))
        self.enterContext(patch.object(emp, "herdr_session", {"zedpane": "zed-agent"}.get))
        # the process tree, as herdr and the kernel report it: the pane this test's process runs in (None: in no pane) and its ancestors
        self.proc_pane, self.ancestry = None, [999, 4242]
        self.enterContext(patch.object(emp, "process_pane", lambda hint=None: self.proc_pane))
        self.enterContext(patch.object(emp, "ancestors", lambda pid=None: self.ancestry))

    def zed_shell(self, **env):
        """Zed's own shell: its process runs in the herdr pane of the session holding the lock (the lock's pid is an ancestor)."""
        self.proc_pane = "zedpane"
        return patch.dict(os.environ, {"HERDR_PANE_ID": "zedpane", **env})

    def as_zed(self, *argv):
        with self.zed_shell():
            return run("finish", "--as-zed", *argv)

    def seed(self, hat, status="running", tid=20, branch="story/ment-20-guard", repo="api", verdict=None,
             pr=None, pr_state=None, plan=None, core_start=None):
        """`plan` is the session's own plan file name under briefs/ (what spawn records from the brief)."""
        self.n += 1
        self.conn.execute(
            "INSERT INTO sessions(id,task_id,repo,base,branch,worktree,agent,status,hat,verdict,pr_url,pr_state,plan,core_start,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.n, tid, repo, "main", branch, "/wt", f"{hat}-t{tid}-{self.n}", status, hat, verdict, pr, pr_state,
             str(self.profile / "briefs" / plan) if plan else None, core_start, f"2026-10-2{self.n % 10} 09:00", "x"))
        self.conn.commit()
        return self.n


class ProcessTreeTests(unittest.TestCase):
    """`process_pane` and `ancestors` against a fake `herdr` and the real process tree."""

    def fake_herdr(self, panes):
        """A `herdr` that lists `panes` ({id: shell_pid}) and answers process-info for each."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        script = Path(tmp.name) / "herdr"
        script.write_text("#!/usr/bin/env python3\nimport json, sys\npanes = %r\na = sys.argv[1:]\n"
                          "if a[:2] == ['pane', 'list']:\n    r = {'panes': [{'pane_id': p} for p in panes]}\n"
                          "else:\n    r = {'process_info': {'shell_pid': panes[a[3]], 'foreground_processes': []}}\n"
                          "print(json.dumps({'result': r}))\n" % panes)
        script.chmod(0o755)
        self.enterContext(patch.dict(os.environ, {"PATH": f"{tmp.name}:{os.environ['PATH']}"}))

    def test_the_pane_is_the_one_whose_shell_is_an_ancestor_whatever_the_hint_says(self):
        chain = emp.ancestors()
        self.assertEqual(chain[0], os.getpid())
        self.assertIn(os.getppid(), chain)
        self.fake_herdr({"w1:p1": 999999, "w1:p2": os.getppid(), "w1:p3": 888888})
        for hint in (None, "w1:p1", "w1:p3", "w1:p2"):  # a forged hint changes nothing
            self.assertEqual(emp.process_pane(hint), "w1:p2", hint)

    def test_a_fake_ps_first_on_path_does_not_change_the_ancestors(self):
        real = emp.ancestors()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake = Path(tmp.name) / "ps"
        fake.write_text("#!/bin/sh\necho 4242\n")  # every pid's parent is the lock's pid
        fake.chmod(0o755)
        with patch.dict(os.environ, {"PATH": f"{tmp.name}:{os.environ['PATH']}"}):
            self.assertEqual(emp.ancestors(), real)
            self.assertNotIn(4242, emp.ancestors())

    def test_a_process_in_no_pane_or_without_herdr_has_none(self):
        self.fake_herdr({"w1:p1": 999999})
        self.assertIsNone(emp.process_pane("w1:p1"))
        with patch.dict(os.environ, {"PATH": "/nonexistent"}):
            self.assertIsNone(emp.process_pane())


class StopGuardTests(AssignDb):
    def stop(self, payload=None, env=None):
        env = {"ZED_HAT": "build", "ZED_SESSION": "S1"} if env is None else env
        with patch.dict(os.environ, env), patch.object(sys, "stdin", io.StringIO(json.dumps(payload or {}))):
            return run("stop-guard")

    def report_done(self):
        log = self.profile / ".cache" / "status" / "S1.log"
        log.parent.mkdir(parents=True)
        log.write_text("needs-decision: x\ndone: built; pr=none; qa=ran it\n")

    def test_a_turn_that_reported_done_without_finish_is_blocked_and_after_finish_it_is_not(self):
        self.seed("build")
        self.assertEqual(self.stop(), (0, "", ""))  # nothing reported yet
        self.report_done()
        code, out, _ = self.stop()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["decision"], "block")
        self.assertIn("zed-employee finish", json.loads(out)["reason"])
        self.assertEqual(self.as_zed("--session", "S1")[0], 0)
        self.assertEqual(self.stop(), (0, "", ""))

    def test_a_second_stop_in_a_row_and_a_session_without_a_hat_pass(self):
        self.seed("build")
        self.report_done()
        self.assertEqual(self.stop({"stop_hook_active": True})[1], "")
        self.assertEqual(self.stop(env={"ZED_SESSION": "S1"})[1], "")

    def test_a_broken_input_fails_open(self):
        with patch.dict(os.environ, {"ZED_HAT": "build", "ZED_SESSION": "S1"}), patch.object(sys, "stdin", io.StringIO("not json")):
            self.assertEqual(run("stop-guard"), (0, "", ""))


class FinishTests(AssignDb):
    def checkout(self, sid):
        """Make session `sid`'s worktree a real checkout with one commit; returns its path."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.git(tmp.name, "init", "-q")
        self.git(tmp.name, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
        self.conn.execute("UPDATE sessions SET worktree=? WHERE id=?", (tmp.name, sid))
        self.conn.commit()
        return tmp.name

    @staticmethod
    def git(path, *a):
        return subprocess.run(["git", "-C", path, *a], check=True, capture_output=True, text=True).stdout.strip()

    def finish(self, *argv, session=1):
        """Zed's own shell finishing session `session`."""
        return self.as_zed("--session", f"S{session}", *argv)

    def in_pane(self, sid, pane, hat="build", **env):
        """The environment of a shell inside session `sid`'s herdr pane."""
        self.conn.execute("UPDATE sessions SET pane_id=? WHERE id=?", (pane, sid))
        self.conn.commit()
        self.proc_pane = pane
        return patch.dict(os.environ, {"HERDR_PANE_ID": pane, "ZED_HAT": hat, **env})

    def test_a_build_finishes_ticks_its_last_step_and_records_the_finish(self):
        sid = self.seed("build", pr="https://github.com/o/r/pull/5", plan="T20-api-plan.md")
        plan = self.profile / "briefs" / "T20-api-plan.md"
        plan.parent.mkdir()
        plan.write_text("- [x] 1. a\n- [x] 2. b\n- [ ] 3. Raise the PR\n- [ ] 4. Run zed-employee finish and report (ZED:DONE)\n")
        code, out, err = self.finish()
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(out.splitlines()[0], f"finished S{sid} (build, T20)")
        self.assertIn("plan 3/4 steps ticked (only merge/push/raise-PR steps open)", out)
        self.assertIn("- [x] 4. Run zed-employee finish", plan.read_text())
        self.assertIn(("finish", f"S{sid} build"), [tuple(r) for r in self.conn.execute("SELECT kind, text FROM events")])
        self.assertEqual(self.conn.execute("SELECT verdict, verdict_tree FROM sessions").fetchone()[:], (None, None))

    def test_the_lessons_and_memory_flags_are_gone(self):
        self.seed("build")
        for argv in (["--lesson", "x"], ["--none", "x"], ["--note", "x"], ["-e", "juno"]):
            with self.assertRaises(SystemExit):
                run("finish", "--session", "S1", *argv)

    def test_a_build_with_an_unticked_plan_step_is_refused_until_it_is_ticked(self):
        self.seed("build", plan="T20-api-plan.md")
        plan = self.profile / "briefs" / "T20-api-plan.md"
        plan.parent.mkdir(exist_ok=True)
        plan.write_text("- [x] 1. a\n- [ ] 2. Fix the parser. Done when: tests\n- [ ] 3. Push and raise PR\n")
        code, out, err = self.finish()
        self.assertEqual(code, 1)
        self.assertIn("step 2: Fix the parser", err)
        self.assertNotIn("step 3", err)
        plan.write_text("- [x] 1-2 done\n- [ ] 3. Push and raise PR\n")
        self.assertIn("catch-all tick", self.finish()[2])
        plan.write_text("- [x] 1. a\n- [x] 2. Fix the parser. Done when: tests\n- [ ] 3. Push and raise PR\n")
        self.assertEqual(self.finish()[0], 0)

    def test_review_and_qa_finish_only_with_their_own_plan_ticked(self):
        self.seed("review", plan="T20-api-review-plan.md")
        plan = self.profile / "briefs" / "T20-api-review-plan.md"
        plan.parent.mkdir(exist_ok=True)
        plan.write_text("- [ ] 1. Read the diff\n- [x] 2. Run the suite\n- [ ] 3. Run zed-employee finish and report (ZED:DONE)\n")
        self.checkout(1)
        code, _, err = self.finish("--verdict", "clean")
        self.assertEqual(code, 1)
        self.assertIn("step 1: Read the diff", err)
        self.assertNotIn("step 3", err)  # finish ticks that one itself
        self.assertIn("- [ ] 3.", plan.read_text())  # and a refusal changed nothing
        plan.write_text("- [x] 1. Read the diff\n- [x] 2. Run the suite\n- [ ] 3. Run zed-employee finish and report (ZED:DONE)\n")
        code, out, _ = self.finish("--verdict", "clean")
        self.assertEqual(code, 0)
        self.assertEqual(plan.read_text().count("- [x]"), 3)
        self.assertIn("plan 3/3 steps ticked", out)

    def test_another_sessions_plan_never_blocks_a_finish(self):
        self.seed("build", plan="T20-api-plan.md")
        self.seed("review", plan="T20-api-review-plan.md")
        (self.profile / "briefs").mkdir()
        (self.profile / "briefs" / "T20-api-plan.md").write_text("- [x] 1. a\n")
        (self.profile / "briefs" / "T20-api-review-plan.md").write_text("- [ ] 1. Still reading\n")
        self.assertEqual(self.finish()[0], 0)

    def test_a_second_finish_is_refused(self):
        self.seed("build")
        self.assertEqual(self.finish()[0], 0)
        code, _, err = self.finish()
        self.assertEqual(code, 1)
        self.assertIn("S1 is already finished", err)

    def test_which_session_the_flag_and_the_environment_pick(self):
        self.seed("build")
        with self.zed_shell():
            self.assertIn("not a hat session", run("finish")[2])  # Zed's shell without --as-zed
        self.assertIn("S9 is not a hat session", self.finish(session=9)[2])
        self.assertIn("needs --session", self.as_zed()[2])
        with self.zed_shell():
            self.assertIn("is not this session", run("finish", "--session", "S1")[2])  # naming one is not enough in Zed's shell either
        with self.in_pane(1, "p1"):
            self.assertEqual(run("finish")[0], 0)

    def reviewed_by_author_setup(self):
        author, review, qa = self.seed("build"), self.seed("review"), self.seed("qa")
        self.checkout(review)
        return author, review, qa

    def assertNothingRecorded(self):
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions WHERE verdict IS NOT NULL").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='finish'").fetchone()[0], 0)

    def test_a_session_finishes_only_itself(self):
        author, review, qa = self.reviewed_by_author_setup()
        with self.in_pane(author, "p1"):
            for sid in (review, qa):
                for argv in (["--session", f"S{sid}"], ["--as-zed", "--session", f"S{sid}"]):
                    code, _, err = run("finish", "--verdict", "clean", *argv)
                    self.assertEqual(code, 1, argv)
                    self.assertTrue("finishes only itself" in err or "--as-zed is for Zed" in err, err)
        self.assertNothingRecorded()
        with self.in_pane(review, "p2", "review"):  # its own pane: its id, named or not
            self.assertEqual(run("finish", "--verdict", "clean", "--session", f"S{review}")[0], 0)

    def test_the_author_cannot_spoof_the_environment_to_finish_another_session(self):
        author, review, qa = self.reviewed_by_author_setup()
        for spoof in ({"ZED_SESSION": f"S{review}"}, {"ZED_SESSION": f"S{qa}"}, {"ZED_SESSION": ""}, {"ZED_SESSION": None}):
            with self.in_pane(author, "p1", ZED_SESSION=f"S{author}"):
                for key, value in spoof.items():  # the author's shell after `ZED_SESSION=... cmd`, `export` or `env -u`
                    os.environ.pop(key) if value is None else os.environ.__setitem__(key, value)
                for argv in (["--session", f"S{review}"], ["--as-zed", "--session", f"S{review}"], ["--as-zed", "--session", f"S{qa}"]):
                    code, _, err = run("finish", "--verdict", "clean", *argv)
                    self.assertEqual(code, 1, (spoof, argv))
        self.assertNothingRecorded()

    def test_a_pane_and_a_zed_session_that_disagree_are_refused(self):
        author, review, _ = self.reviewed_by_author_setup()
        with self.in_pane(author, "p1", ZED_SESSION=f"S{review}"):
            self.assertIn("runs in the pane of S1 but ZED_SESSION says S2", run("finish", "--verdict", "clean")[2])
        self.assertNothingRecorded()

    def test_a_hat_claim_that_disagrees_with_the_panes_session_is_refused(self):
        sid = self.seed("build")
        with self.in_pane(sid, "p1", ZED_HAT="review", ZED_SESSION=f"S{sid}"):
            code, _, err = run("finish")
        self.assertEqual(code, 1)
        self.assertIn(f"runs in the pane of S{sid} (build) but ZED_HAT says review", err)
        self.assertNothingRecorded()

    def test_a_hat_shell_that_lost_its_identity_or_claims_to_be_zed_is_refused(self):
        self.reviewed_by_author_setup()
        with patch.dict(os.environ, {"ZED_HAT": "build"}):  # no pane, no ZED_SESSION: cannot be told from Zed's shell by the id alone
            for argv in (["--as-zed", "--session", "S2"], ["--session", "S2"], []):
                code, _, err = run("finish", "--verdict", "clean", *argv)
                self.assertEqual(code, 1, argv)
            self.assertIn("cannot be read", err)
        self.assertNothingRecorded()

    def test_a_hat_shell_in_no_herdr_pane_has_no_identity_and_zed_still_finishes_a_stuck_worker(self):
        author, review, _ = self.reviewed_by_author_setup()
        for env in ({"ZED_HAT": "build", "ZED_SESSION": f"S{author}"}, {"ZED_SESSION": f"S{author}"}):  # claims, but no pane hosts this process
            with patch.dict(os.environ, env):
                self.assertIn("cannot be read", run("finish", "--verdict", "clean", "--session", f"S{review}")[2])
                self.assertIn("cannot be read", run("finish", "--verdict", "clean")[2])
        self.assertNothingRecorded()
        self.assertEqual(self.finish("--verdict", "clean", session=review)[0], 0)  # Zed's shell: --as-zed names the session

    def test_the_identity_comes_from_the_process_not_from_the_variables(self):
        """Forged HERDR_PANE_ID / ZED_SESSION / ZED_HAT (`V=HERDR_PANE_ID; export $V=...`) from a process that is not in that pane."""
        author, review, qa = self.reviewed_by_author_setup()
        self.conn.execute("UPDATE sessions SET pane_id='p2' WHERE id=?", (review,))
        self.conn.execute("UPDATE sessions SET pane_id='p3' WHERE id=?", (qa,))
        self.conn.commit()
        forged = {"HERDR_PANE_ID": "p3", "ZED_SESSION": f"S{qa}", "ZED_HAT": "qa"}  # all three: the QA session's
        for label, pane in (("a process in the author's pane", "p1"), ("a process in no pane", None), ("a process in Zed's pane", "zedpane")):
            self.conn.execute("UPDATE sessions SET pane_id='p1' WHERE id=?", (author,))
            self.conn.commit()
            self.proc_pane = pane
            with patch.dict(os.environ, forged):
                for argv in ([], ["--session", f"S{qa}"], ["--as-zed", "--session", f"S{qa}"]):
                    code, _, err = run("finish", "--verdict", "clean", *argv)
                    self.assertEqual(code, 1, (label, argv))
                    self.assertIn("HERDR_PANE_ID says p3 but this process runs in", err, label)
        self.assertNothingRecorded()
        with self.in_pane(review, "p2", "review"):  # the real thing, from inside its own pane, still works
            self.assertEqual(run("finish", "--verdict", "clean")[0], 0)

    def test_a_forged_zed_pane_without_the_locks_process_among_the_ancestors_is_refused(self):
        _, review, _ = self.reviewed_by_author_setup()
        for env in ({"HERDR_PANE_ID": "zedpane"}, {}):  # the pane named in the lock (readable by anyone), claimed or not
            for label, ancestry in (("pid not an ancestor", [999, 1234]), ("no ancestry", [])):
                self.ancestry = ancestry
                self.proc_pane = "zedpane"  # even a process that really is in Zed's pane (the lock's pid is not above it)
                with patch.dict(os.environ, env):
                    code, _, err = run("finish", "--as-zed", "--verdict", "clean", "--session", f"S{review}")
                self.assertEqual((code, "--as-zed is for Zed" in err), (1, True), (env, label))
        self.assertNothingRecorded()
        self.ancestry = [999, 4242]
        self.assertEqual(self.finish("--verdict", "clean", session=review)[0], 0)  # the real lock holder: its pid is an ancestor

    def test_as_zed_needs_a_positive_identity_not_just_the_absence_of_one(self):
        """The author strips every identity variable (`env -uZED_SESSION -uZED_HAT -uHERDR_PANE_ID ...`): that must not read as Zed."""
        author, review, qa = self.reviewed_by_author_setup()
        shells = {"every variable stripped": ({}, None), "only ZED_HAT left": ({"ZED_HAT": "build"}, None),
                  "only ZED_SESSION left": ({"ZED_SESSION": f"S{author}"}, None), "an unknown pane": ({"HERDR_PANE_ID": "elsewhere"}, None),
                  "the pane of a hat session": ({"HERDR_PANE_ID": "p1"}, "p1"), "stripped, in the hat's own pane": ({}, "p1")}
        self.conn.execute("UPDATE sessions SET pane_id='p1' WHERE id=?", (author,))
        self.conn.commit()
        for label, (env, pane) in shells.items():
            self.proc_pane = pane
            with patch.dict(os.environ, env):
                for sid in (review, qa):
                    code, _, err = run("finish", "--as-zed", "--verdict", "clean", "--session", f"S{sid}")
                    self.assertEqual(code, 1, label)
                    self.assertTrue(any(m in err for m in ("--as-zed is for Zed", "cannot be read", "HERDR_PANE_ID says")), (label, err))
        self.assertNothingRecorded()

    def test_as_zed_is_refused_when_the_lock_or_herdr_cannot_confirm_the_pane(self):
        _, review, _ = self.reviewed_by_author_setup()
        lock = self.profile / ".cache" / "zed.lock"
        for label, content, herdr in (("no lock file", None, "zed-agent"), ("a lock without a session", {"pane": "zedpane"}, "zed-agent"),
                                      ("another pane holds it", {"pane": "other", "agent_session": "zed-agent"}, "zed-agent"),
                                      ("herdr says another session", {"pane": "zedpane", "agent_session": "zed-agent"}, "someone-else"),
                                      ("herdr cannot say", {"pane": "zedpane", "agent_session": "zed-agent"}, None),
                                      ("a lock without a pid", {"pane": "zedpane", "agent_session": "zed-agent"}, "zed-agent"),
                                      ("another pid holds it", {"pane": "zedpane", "agent_session": "zed-agent", "pid": 1234}, "zed-agent")):
            lock.unlink(missing_ok=True) if content is None else lock.write_text(json.dumps(content))
            with patch.object(emp, "herdr_session", lambda pane, herdr=herdr: herdr):
                code, _, err = self.as_zed("--verdict", "clean", "--session", f"S{review}")
            self.assertEqual(code, 1, label)
            self.assertIn("--as-zed is for Zed", err, label)
        self.assertNothingRecorded()

    def test_malformed_session_ids_are_refused_with_a_message_not_a_traceback(self):
        self.seed("build")
        for env, argv in (({"ZED_SESSION": "abc"}, []), ({"ZED_SESSION": "s2"}, []), ({"ZED_SESSION": "2x"}, []),
                          ({"HERDR_PANE_ID": "zedpane"}, ["--as-zed", "--session", "s2"]),
                          ({"HERDR_PANE_ID": "zedpane"}, ["--as-zed", "--session", "S"]), ({"ZED_SESSION": "S1 "}, [])):
            pane = "zedpane" if "--as-zed" in argv else "p1"
            self.conn.execute("UPDATE sessions SET pane_id='p1' WHERE id=1")
            self.conn.commit()
            self.proc_pane = pane  # in the pane that fits the call: only the id is malformed
            with patch.dict(os.environ, {"HERDR_PANE_ID": pane, **env}):
                code, _, err = run("finish", *argv)
            self.assertEqual(code, 1, (env, argv))
            self.assertNotIn("Traceback", err)
            self.assertIn("not a session id", err)

    def test_review_and_qa_need_a_verdict_and_blockers_need_a_draft(self):
        self.seed("review")
        wt = self.checkout(1)
        code, _, err = self.finish()
        self.assertEqual((code, "needs --verdict clean|blockers" in err), (1, True))
        code, _, err = self.finish("--verdict", "blockers")
        self.assertIn("needs --draft", err)
        code, _, err = self.finish("--verdict", "blockers", "--draft", str(self.root / "missing.md"))
        self.assertIn("does not exist", err)
        draft = self.root / "findings.md"
        draft.write_text("- src/a.py:3 high: fix\nverdict: blockers\n")
        code, out, err = self.finish("--verdict", "blockers", "--draft", str(draft))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(tuple(self.conn.execute("SELECT verdict, verdict_tree FROM sessions WHERE id=1").fetchone()),
                         ("blockers", self.git(wt, "rev-parse", "HEAD^{tree}")))  # a repo that is not Zed: its tree id
        placed = self.profile / "briefs" / "T20-review-S1-findings.md"  # copied to where the hat may write and fixers look
        self.assertEqual(placed.read_text(), draft.read_text())
        self.assertEqual(self.conn.execute("SELECT text FROM events WHERE kind='findings'").fetchone()[0],
                         f"S1 review blockers: {placed}")
        self.assertIn("verdict blockers, findings", out)

    def test_a_draft_already_in_place_is_left_alone(self):
        self.seed("review")
        self.checkout(1)
        placed = self.profile / "briefs" / "T20-review-S1-findings.md"
        placed.parent.mkdir()
        placed.write_text("- a.py:1 high: x\n")
        self.assertEqual(self.finish("--verdict", "blockers", "--draft", str(placed))[0], 0)
        self.assertEqual(placed.read_text(), "- a.py:1 high: x\n")

    def test_a_clean_review_needs_no_draft_and_a_build_takes_no_verdict(self):
        self.seed("qa")
        self.checkout(1)
        self.assertEqual(self.finish("--verdict", "clean")[0], 0)
        self.seed("build")
        code, _, err = self.finish("--verdict", "clean", session=2)
        self.assertEqual(code, 1)
        self.assertIn("--verdict is for review and qa sessions; this one wore build", err)

    def test_a_review_that_left_the_code_as_it_found_it_gives_its_verdict(self):
        self.seed("review")
        wt = self.checkout(1)
        self.conn.execute("UPDATE sessions SET core_start=? WHERE id=1", (zedlib.core_tree(wt),))
        self.conn.commit()
        Path(wt, "notes.txt").write_text("untracked scratch\n")  # a file that is not committed does not change the code
        self.assertEqual(self.finish("--verdict", "clean")[0], 0)

    def test_a_review_that_changed_the_code_is_its_author_and_gives_no_verdict(self):
        self.seed("review")
        wt = self.checkout(1)
        self.conn.execute("UPDATE sessions SET core_start=? WHERE id=1", (zedlib.core_tree(wt),))
        self.conn.commit()
        Path(wt, "fix.py").write_text("x = 1\n")
        self.git(wt, "add", ".")
        self.git(wt, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "fix it myself")
        code, _, err = self.finish("--verdict", "clean")
        self.assertEqual(code, 1)
        self.assertIn("changed during this review session: whoever changes it is its author", err)
        self.assertEqual(self.conn.execute("SELECT verdict, verdict_tree FROM sessions").fetchone()[:], (None, None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def test_a_verdict_is_refused_when_the_commit_cannot_be_read(self):
        self.seed("qa")  # its worktree /wt is not a checkout
        code, _, err = self.finish("--verdict", "clean")
        self.assertEqual(code, 1)
        self.assertIn("cannot read the commit", err)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)

    def set_in_place(self):
        path = self.profile / "config" / "projects.json"
        path.write_text(json.dumps({"repos": {"api": {"path": "/wt", "worktrees": False}}}))

    def test_a_verdict_in_a_non_git_in_place_project_records_a_nogit_stand_in(self):
        self.set_in_place()
        self.seed("qa")
        self.assertEqual(self.finish("--verdict", "clean")[0], 0)
        row = self.conn.execute("SELECT verdict, verdict_tree FROM sessions WHERE id=1").fetchone()
        self.assertEqual(row[0], "clean")
        self.assertRegex(row[1], r"^nogit:[0-9a-f]{64}$")

    def test_the_nogit_stand_in_hashes_the_findings_draft(self):
        self.set_in_place()
        self.seed("review")
        draft = self.profile / "d.md"
        draft.write_text("- a.py:1 high: x\n")
        self.assertEqual(self.finish("--verdict", "blockers", "--draft", str(draft))[0], 0)
        self.assertEqual(self.conn.execute("SELECT verdict_tree FROM sessions WHERE id=1").fetchone()[0], emp.nogit_stand_in(1, draft))
        self.assertNotEqual(emp.nogit_stand_in(1, draft), emp.nogit_stand_in(1, None))

    def test_refusals_change_nothing(self):
        self.seed("review")
        self.checkout(1)
        self.finish("--verdict", "blockers")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT verdict FROM sessions").fetchone()[0], None)


if __name__ == "__main__":
    unittest.main()
