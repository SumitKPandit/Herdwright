"""Step 3.6, end to end with the real spawn, finish, suggest and board code (herdr is stubbed): the pilot pair
Juno and Quill take one story through build, a refused self-review, review, a fix, and a clean re-review and QA.
Set ZED_FLOW_TRANSCRIPT=<file> to write what each step printed."""
import os
import shutil
import unittest

import _load  # noqa: F401

import zedemployee as emp
import zedmem
from test_zed_assign import AssignCase
from test_zed_finish import run


class PilotFlow(AssignCase):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.profile / "employees" / "pip")  # two is the minimum that can satisfy the hard rule
        self.transcript = []

    def say(self, title, *lines):
        self.transcript += [f"## {title}", *lines, ""]

    def board(self):
        out = run("board")[1].rstrip()
        self.say("board", *out.splitlines())
        return out

    def assignment(self, employee, hat, **kw):
        sid = self.spawn(employee, hat, **kw)
        s = self.session(sid)
        self.say(f"S{sid}: {employee} wears {hat}", f"agent {s['agent']}, origin {s['origin']}, pack {emp.pack_path(employee, f'S{sid}').name}")
        return sid

    def end(self, sid, *argv):
        employee = self.session(sid)["employee"]
        if "--lesson" in argv and not {"--idea", "--no-idea"} & set(argv):
            argv += ("--no-idea", "nothing here can be code")
        code, out, err = run("finish", "-e", employee, "--session", f"S{sid}", *argv)
        self.assertEqual(code, 0, err)
        self.say(f"S{sid}: {employee} finishes", *out.splitlines())
        self.conn.execute("UPDATE sessions SET status='finished' WHERE id=?", (sid,))
        self.conn.commit()

    def test_build_refused_self_review_review_fix_rereview_and_qa(self):
        # 1. Juno builds
        build = self.assignment("juno", "build", tags="auth")
        self.end(build, "--lesson", "Run the checks before the self-review.")
        self.assertIn("review  T20 api story/ment-20-guard  may take it: quill", self.board())

        # 2. the hard rule: Juno may not review or QA their own branch
        self.output.truncate(0)
        with self.assertRaises(SystemExit):
            self.spawn("juno", "review")
        self.say("juno review: refused", *[l for l in self.output.getvalue().splitlines() if "nobody reviews" in l])
        self.assertIn("nobody reviews or QAs their own work", self.output.getvalue())
        code, out, _ = run("suggest", "T20", "--hat", "review")
        self.say("suggest review", *out.splitlines())
        self.assertIn("1. quill", out)
        self.assertIn("juno     not eligible: authored this work", out)

        # 3. Quill reviews: blockers, with a findings draft
        review = self.assignment("quill", "review")
        draft = self.root / "findings-s2.md"
        draft.write_text("- guard.py:1 high: require_role is skipped on the error path. Fix: call it first.\nverdict: blockers\n")
        self.end(review, "--verdict", "blockers", "--draft", str(draft), "--none", "first review of this repo")
        self.assertIn("fix     T20 api story/ment-20-guard  may take it: juno, quill", self.board())

        # 4. the fix is a build whose first prompt names the findings; Juno ranks first (they built it)
        self.assertEqual(run("suggest", "T20", "--hat", "build")[1].splitlines()[1].split()[1], "juno")
        fix = self.assignment("juno", "build")
        self.assertIn(str(draft.resolve()), self.mock_prompt_worker.call_args.args[1])
        self.end(fix, "--lesson", "Start a fix from the findings draft, one plan step per finding.")

        # 5. the author set only grows: Quill (never an author) re-reviews and QAs
        self.assertIn("review  T20", self.board())
        again = self.assignment("quill", "review")
        self.end(again, "--verdict", "clean", "--none", "nothing new")
        qa = self.assignment("quill", "qa")
        self.end(qa, "--verdict", "clean", "--none", "nothing new")
        self.assertIn("ready   T20 api story/ment-20-guard  zed-pr ready-check, then tell the user", self.board())

        # log.md and memory: one line per assignment with its hat; every session wrote a work entry
        juno_log = (self.profile / "employees" / "juno" / "log.md").read_text().splitlines()[-2:]
        quill_log = (self.profile / "employees" / "quill" / "log.md").read_text().splitlines()[-3:]
        self.say("juno log.md", *juno_log)
        self.say("quill log.md", *quill_log)
        self.assertEqual([l.split(" | ")[3] + ":" + l.split(" | ")[6] for l in juno_log], ["build:-", "build:-"])
        self.assertEqual([l.split(" | ")[3] + ":" + l.split(" | ")[6] for l in quill_log], ["review:blockers", "review:clean", "qa:clean"])
        work = [e for _, e in zedmem.all_entries(emp.employee_dir("quill")) if e.kind == "work"]
        self.assertEqual([e.get("hat") for e in work], ["review", "review", "qa"])
        if path := os.environ.get("ZED_FLOW_TRANSCRIPT"):
            with open(path, "w") as f:
                f.write("# Pilot flow transcript (stubbed herdr)\n\n" + "\n".join(self.transcript))


if __name__ == "__main__":
    unittest.main()
