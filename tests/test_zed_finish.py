"""`zed-employee finish` (design step 3.3): the write-back an assignment ends with."""
import unittest
from unittest.mock import patch

import _load  # noqa: F401

import zedassign
import zedemployee as emp
import zedmem
from test_zed_employee import ProfileCase, run

SESSIONS = """CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY, task_id INTEGER, repo TEXT, base TEXT, branch TEXT,
  worktree TEXT, agent TEXT, pr_url TEXT, pr_state TEXT, status TEXT DEFAULT 'running', created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, title TEXT, status TEXT DEFAULT 'active');
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, task_id INTEGER, ts TEXT, kind TEXT, text TEXT);"""


class AssignDb(ProfileCase):
    """A profile with a task DB, Juno and Quill hired, and helpers that seed assignments straight into the DB."""

    def setUp(self):
        super().setUp()
        self.conn = emp.connect()
        self.addCleanup(self.conn.close)
        self.conn.executescript(SESSIONS)
        emp.zedlib.ensure_employee_schema(self.conn)
        for name in ("juno", "quill", "pip"):
            emp.hire(name)
        self.conn.execute("INSERT INTO tasks(id, title) VALUES (20, 'Role guard'), (21, 'Other')")
        self.conn.commit()
        self.n = 0

    def seed(self, employee, hat, status="running", tid=20, branch="story/ment-20-guard", repo="api", verdict=None,
             pr=None, pr_state=None, created=None):
        self.n += 1
        self.conn.execute(
            "INSERT INTO sessions(id,task_id,repo,base,branch,worktree,agent,status,employee,hat,verdict,pr_url,pr_state,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.n, tid, repo, "main", branch, "/wt", f"{employee}-{self.n}", status, employee, hat, verdict, pr, pr_state,
             created or f"2026-10-2{self.n % 10} 09:00", "x"))
        self.conn.commit()
        return self.n


class FinishTests(AssignDb):
    def finish(self, *argv):
        return run("finish", *argv)

    def test_a_build_finishes_with_a_lesson_and_writes_everything_back(self):
        sid = self.seed("juno", "build", pr="https://github.com/o/r/pull/5")
        (self.profile / "briefs").mkdir()
        (self.profile / "briefs" / "T20-api-plan.md").write_text("- [x] 1. a\n- [x] 2. b\n- [ ] 3. c\n")
        code, out, err = self.finish("-e", "juno", "--lesson", "Run the checks before the self-review.", "--note", "Tricky: the guard is cached.")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(out.splitlines()[0], f"finished S{sid} (juno, build, T20)")
        self.assertIn("plan 2/3 steps ticked (not all: say why in your ZED:DONE)", out)
        self.assertIn("Did you do anything by hand more than once", out)
        edir = emp.employee_dir("juno")
        work = zedmem.load(edir / "memory" / "repos" / "api.md")[1][0]
        self.assertEqual(work.kind, "work")
        self.assertEqual(work.get("hat"), "build")
        self.assertEqual(work.get("src"), f"T20 S{sid} https://github.com/o/r/pull/5")
        self.assertEqual(work.body, f"S{sid} build on T20 (Role guard), branch story/ment-20-guard: "
                                    "PR https://github.com/o/r/pull/5; plan 2/3 steps. Tricky: the guard is cached.")
        lesson = zedmem.load(edir / "memory" / "craft" / "build.md")[1][0]
        self.assertEqual((lesson.kind, lesson.get("src")), ("lesson", f"T20 S{sid}"))
        log = (edir / "log.md").read_text().splitlines()[-1]
        self.assertRegex(log, rf"^- \d{{4}}-\d{{2}}-\d{{2}} \| S{sid} \| T20 \| build \| api \| story/ment-20-guard \| - \| https://github.com/o/r/pull/5 \| plan 2/3$")
        events = [tuple(r) for r in self.conn.execute("SELECT kind, text FROM events ORDER BY id")]
        self.assertIn(("finish", f"S{sid} juno build"), events)
        self.assertTrue(any(k == "memory" and "work m-0002" in t and "lesson m-0001" in t for k, t in events))

    def test_a_second_finish_is_refused(self):
        self.seed("juno", "build")
        self.assertEqual(self.finish("-e", "juno", "--none", "nothing new")[0], 0)
        self.conn.execute("UPDATE sessions SET status='running'")  # as if the same session were open again
        self.conn.commit()
        code, _, err = self.finish("-e", "juno", "--none", "again", "--session", "S1")
        self.assertEqual(code, 1)
        self.assertIn("S1 is already finished", err)

    def test_review_and_qa_need_a_verdict_and_blockers_need_a_draft(self):
        self.seed("quill", "review")
        code, _, err = self.finish("-e", "quill", "--none", "x")
        self.assertEqual((code, "needs --verdict clean|blockers" in err), (1, True))
        code, _, err = self.finish("-e", "quill", "--none", "x", "--verdict", "blockers")
        self.assertIn("needs --draft", err)
        code, _, err = self.finish("-e", "quill", "--none", "x", "--verdict", "blockers", "--draft", str(self.root / "missing.md"))
        self.assertIn("does not exist", err)
        draft = self.root / "findings.md"
        draft.write_text("- src/a.py:3 high: fix\nverdict: blockers\n")
        code, out, err = self.finish("-e", "quill", "--none", "x", "--verdict", "blockers", "--draft", str(draft))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.conn.execute("SELECT verdict FROM sessions WHERE id=1").fetchone()[0], "blockers")
        self.assertEqual(self.conn.execute("SELECT text FROM events WHERE kind='findings'").fetchone()[0],
                         f"S1 quill review blockers: {draft.resolve()}")
        self.assertIn("verdict blockers, findings", out)

    def test_a_clean_review_needs_no_draft_and_a_build_takes_no_verdict(self):
        self.seed("quill", "qa")
        self.assertEqual(self.finish("-e", "quill", "--none", "x", "--verdict", "clean")[0], 0)
        self.seed("juno", "build")
        code, _, err = self.finish("-e", "juno", "--none", "x", "--verdict", "clean")
        self.assertEqual(code, 1)
        self.assertIn("--verdict is for review and qa assignments; this one wore build", err)

    def test_a_lesson_or_none_is_required_but_not_both(self):
        self.seed("juno", "build")
        for argv in ([], ["--none", "x", "--lesson", "y"]):
            code, _, err = self.finish("-e", "juno", *argv)
            self.assertEqual(code, 1)
            self.assertIn("at least one --lesson", err)

    def test_refusals_change_nothing(self):
        self.seed("juno", "build")
        self.finish("-e", "juno", "--lesson", "one lesson", "--lesson", "one   LESSON")  # duplicate of the first
        self.assertEqual(zedmem.load(emp.employee_dir("juno") / "memory" / "craft" / "build.md")[1], [])
        self.assertFalse(zedmem.load(emp.employee_dir("juno") / "memory" / "repos" / "api.md")[1])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        code, _, err = self.finish("-e", "juno", "--lesson", "token = abcdef123456 stays here")
        self.assertIn("looks like a secret", err)

    def test_unresolved_stale_entries_block_finish(self):
        self.seed("juno", "build")
        edir = emp.employee_dir("juno")
        entry = zedmem.remember(edir, "fact", "Role checks live in guard.py.", "guard.py", repo="api", anchor="def require_role")
        path = edir / "memory" / "repos" / "api.md"
        pre, entries = zedmem.load(path)
        entries[0].fields["stale"] = "2026-10-27"
        zedmem.save(path, pre, entries)
        code, _, err = self.finish("-e", "juno", "--none", "x")
        self.assertEqual(code, 1)
        self.assertIn(f"{entry.id}", err)
        self.assertEqual(run("drop", "-e", "juno", entry.id, "--reason", "gone")[0], 0)
        self.assertEqual(self.finish("-e", "juno", "--none", "x")[0], 0)

    def test_ideas_go_to_ideas_md_and_silence_the_question(self):
        self.seed("juno", "build")
        code, out, _ = self.finish("-e", "juno", "--none", "x", "--idea", "a script to list changed migrations")
        self.assertNotIn("Did you do anything by hand", out)
        self.assertIn("1 idea(s) added to ideas.md", out)
        line = (self.profile / "employees" / "ideas.md").read_text().splitlines()[-1]
        self.assertRegex(line, r"^- \d{4}-\d{2}-\d{2} \| juno \| S1 \| a script to list changed migrations$")

    def test_which_assignment_the_session_flag_and_the_environment_pick(self):
        self.seed("juno", "build")
        self.seed("juno", "review", tid=21)
        code, _, err = self.finish("-e", "juno", "--none", "x")
        self.assertIn("2 open assignments: say which", err)
        with patch.dict("os.environ", {"ZED_SESSION": "S1"}):
            self.assertEqual(self.finish("-e", "juno", "--none", "x")[0], 0)
        self.assertIn("no open assignment", self.finish("-e", "pip", "--none", "x")[2])
        self.assertIn("is not an assignment of pip", self.finish("-e", "pip", "--none", "x", "--session", "S2")[2])

    def test_same_inputs_give_the_same_output(self):
        outs = []
        for i in range(2):
            self.n = 0
            self.conn.execute("DELETE FROM sessions")
            self.conn.execute("DELETE FROM events")
            for p in (emp.employee_dir("juno") / "memory").rglob("*.md"):
                p.write_text("")
            self.seed("juno", "build")
            outs.append(self.finish("-e", "juno", "--lesson", "same lesson", "--session", "S1")[1])
        self.assertEqual(outs[0], outs[1])


class SuggestTests(AssignDb):
    def names(self, hat, tid=20, **kw):
        rows = zedassign.suggest(self.conn, tid, hat, "api", "story/ment-20-guard", **kw)
        return [r["name"] for r in rows if r["eligible"]]

    def test_authors_are_not_eligible_for_review_or_qa_and_busy_people_are_never(self):
        self.seed("juno", "build", status="finished")
        self.seed("pip", "build", status="finished")  # the fixer joins the author set
        self.assertEqual(self.names("review"), ["quill"])
        self.assertEqual(self.names("qa"), ["quill"])
        rows = {r["name"]: r for r in zedassign.suggest(self.conn, 20, "review", "api", "story/ment-20-guard")}
        self.assertEqual(rows["juno"]["why"], "authored this work")
        self.seed("quill", "review")
        self.assertEqual(self.names("qa"), [])
        self.assertEqual({r["name"]: r["why"] for r in zedassign.suggest(self.conn, 20, "qa", "api", "b")}["quill"], "busy with S3")

    def test_a_designer_counts_as_an_author(self):
        self.seed("pip", "design", status="finished")
        self.assertNotIn("pip", self.names("review"))
        self.assertIn("pip", self.names("build"))

    def test_seniors_first_for_design_and_ambiguous_work_only(self):
        emp.hire("atlas")
        self.assertEqual(self.names("build")[0], "atlas")  # first alphabetically on a full tie
        self.seed("atlas", "build", status="finished", tid=21, repo="web")  # atlas was assigned most recently now
        self.assertEqual(self.names("build")[-1], "atlas")
        with patch.object(emp, "hat_names", return_value=["build", "design", "qa", "review"]):  # the hat ships in step 7
            rows = zedassign.suggest(self.conn, 20, "design", "api")
        self.assertEqual([r["name"] for r in rows][0], "atlas")  # lead hat: senior first despite being last-assigned
        self.assertTrue(rows[0]["lead"])
        self.assertEqual(self.names("build", ambiguous=True)[0], "atlas")
        self.assertNotEqual(self.names("review")[0], "atlas")

    def test_familiarity_memory_tags_task_history_and_hat_history(self):
        zedmem.remember(emp.employee_dir("quill"), "preference", "Approve only with no blockers.", "decision T7 E12", repo="api", tags=["prs"])
        zedmem.remember(emp.employee_dir("pip"), "preference", "Prefer small diffs here.", "decision T8 E1", repo="api", tags=["auth", "views"])
        zedmem.remember(emp.employee_dir("pip"), "preference", "Keep views thin.", "decision T8 E2", repo="api", tags=["auth"])
        rows = {r["name"]: r for r in zedassign.suggest(self.conn, 20, "build", "api", tags=["auth"])}
        self.assertEqual(rows["pip"]["parts"], {"memory": 4, "task": 0, "hat": 0})  # two entries, each 1 + 1 for the matching tag
        self.assertEqual(rows["quill"]["parts"]["memory"], 1)
        self.assertEqual(rows["juno"]["score"], 0)
        self.assertEqual([r["name"] for r in zedassign.suggest(self.conn, 20, "build", "api", tags=["auth"])][:3], ["pip", "quill", "juno"])
        self.seed("juno", "build", status="finished", tid=20)  # juno built this task: the fix goes to them first
        rows = {r["name"]: r for r in zedassign.suggest(self.conn, 20, "build", "api", tags=["auth"])}
        self.assertEqual(rows["juno"]["parts"], {"memory": 0, "task": 5, "hat": 1})
        self.assertEqual(zedassign.suggest(self.conn, 20, "build", "api", tags=["auth"])[0]["name"], "juno")

    def test_stale_memory_does_not_count(self):
        edir = emp.employee_dir("pip")
        zedmem.remember(edir, "fact", "Role checks live in guard.py.", "guard.py", repo="api", anchor="def require_role")
        path = edir / "memory" / "repos" / "api.md"
        pre, entries = zedmem.load(path)
        entries[0].fields["stale"] = "2026-10-27"
        zedmem.save(path, pre, entries)
        self.assertEqual(zedassign.familiarity(self.conn, "pip", 20, "api", "build")[0], 0)

    def test_least_recently_assigned_then_name_break_ties_and_the_order_is_reproducible(self):
        self.seed("juno", "build", status="finished", tid=21, repo="web", created="2026-10-25 09:00")
        self.seed("quill", "build", status="finished", tid=21, repo="web", created="2026-10-20 09:00")
        # pip was never assigned: first; then quill (older), then juno
        expected = ["pip", "quill", "juno"]
        self.assertEqual(self.names("build"), expected)
        self.assertEqual(self.names("build"), expected)
        self.assertEqual(run("suggest", "T20", "--hat", "build", "--repo", "api")[1], run("suggest", "T20", "--hat", "build", "--repo", "api")[1])

    def test_cli_prints_the_numbers_and_the_reason(self):
        self.seed("juno", "build", status="finished")
        code, out, _ = run("suggest", "T20", "--hat", "review")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "suggest T20 --hat review (repo api, branch story/ment-20-guard)")
        self.assertRegex(lines[1], r"^1\. pip +score 0 \(memory 0, task 0, hat 0\)")
        self.assertIn("-  juno     not eligible: authored this work", lines)
        self.assertEqual(run("suggest", "T99", "--hat", "review")[0], 2)
        self.assertEqual(run("suggest", "T20", "--hat", "juggle")[0], 2)


class BoardTests(AssignDb):
    def waits(self):
        return [(w["kind"], w["task"], w["may"]) for w in zedassign.board(self.conn)["waits"]]

    def test_free_people_and_untouched_tasks(self):
        self.conn.execute("UPDATE tasks SET status='intake' WHERE id=21")
        self.conn.commit()
        self.seed("juno", "build")
        board = zedassign.board(self.conn)
        self.assertEqual(board["free"], ["pip", "quill"])
        self.assertEqual(board["waits"], [{"kind": "new", "task": 21, "repo": None, "branch": None, "may": ["pip", "quill"]}])

    def test_a_finished_build_waits_for_review_and_qa_by_non_authors(self):
        self.seed("juno", "build", status="finished")
        self.assertEqual(self.waits(), [("review", 20, ["pip", "quill"]), ("qa", 20, ["pip", "quill"])])
        self.seed("quill", "review")  # a review is open: it no longer waits, and quill is busy
        self.assertEqual(self.waits(), [("qa", 20, ["pip"])])

    def test_blockers_make_a_fix_request_that_the_author_ranks_first_for(self):
        self.seed("juno", "build", status="finished")
        self.seed("quill", "review", status="finished", verdict="blockers")
        # juno built it (task 5 + hat 1), quill reviewed it (task 5), pip knows nothing of it
        self.assertEqual(self.waits(), [("fix", 20, ["juno", "quill", "pip"])])
        self.seed("juno", "build")  # the fix is under way: nothing waits
        self.assertEqual(self.waits(), [])

    def test_a_new_build_resets_the_verdicts_and_two_clean_verdicts_mean_ready(self):
        self.seed("juno", "build", status="finished")
        self.seed("quill", "review", status="finished", verdict="clean")
        self.seed("pip", "qa", status="finished", verdict="blockers")
        self.seed("juno", "build", status="finished")  # the fix landed: both checks start again
        self.assertEqual([w[0] for w in self.waits()], ["review", "qa"])
        self.seed("quill", "review", status="finished", verdict="clean")
        self.seed("pip", "qa", status="finished", verdict="clean")
        self.assertEqual(self.waits(), [("ready", 20, [])])

    def test_finished_work_stays_off_the_board(self):
        self.seed("juno", "build", status="finished", pr_state="merged")
        self.assertEqual(self.waits(), [])
        self.conn.execute("UPDATE sessions SET pr_state=NULL")
        self.conn.execute("UPDATE tasks SET status='done'")
        self.conn.commit()
        self.assertEqual(self.waits(), [])

    def test_cli_renders_and_brief_signals_with_the_exit_code(self):
        self.seed("juno", "build", status="finished")
        code, out, _ = run("board")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[:2], ["free: juno, pip, quill", "waiting:"])
        self.assertIn("  review  T20 api story/ment-20-guard  may take it: pip, quill", out)
        self.assertEqual(run("board", "--brief")[0], 1)
        self.conn.execute("DELETE FROM sessions")
        self.conn.commit()
        code, out, _ = run("board", "--brief")
        self.assertEqual(code, 0)
        self.assertIn("  nothing", out)


if __name__ == "__main__":
    unittest.main()
