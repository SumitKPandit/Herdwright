import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _load

import hwemployee as emp
import hwlib

cli = _load.load("hw-employee")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ProfileCase(unittest.TestCase):
    """A throwaway profile with a real git repo registered as 'api'."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.profile = self.root / "profile"
        self.repo = self.root / "api"
        (self.profile / "config").mkdir(parents=True)
        self.repo.mkdir()
        for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            hwlib.git(self.repo, *cmd)
        (self.repo / "guard.py").write_text("def require_role(user): ...\n")
        hwlib.git(self.repo, "add", ".")
        hwlib.git(self.repo, "commit", "-qm", "init")
        (self.profile / "config" / "projects.json").write_text(
            json.dumps({"repos": {"api": {"path": str(self.repo), "dev_base": "main"}}}))
        for name, value in (("PROFILE_DIR", self.profile), ("DB_PATH", self.profile / "db" / "hw.db"),
                            ("PROJECTS_PATH", self.profile / "config" / "projects.json")):
            patcher = patch.object(hwlib, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class TemplateTests(unittest.TestCase):
    def test_every_manifest_validates(self):
        self.assertEqual(emp.hat_names(), ["build", "qa", "review"])
        for hat in emp.hat_names():
            self.assertEqual(emp.validate_manifest(emp.load_manifest(hat), emp.HATS / hat), [], hat)

    def test_manifest_schema_refusals(self):
        good = emp.load_manifest("review")
        cases = {
            "scripts": {**good, "scripts": "hw-pr"},
            "skills": {**good, "skills": [""]},
            "deny": {**good, "deny": {"edit": ["**"]}},
            "subagents": {**good, "subagents": "many"},
            "kind": {**good, "kind": ""},
            "unknown keys": {**good, "mcp": []},
        }
        for needle, manifest in cases.items():
            self.assertIn(needle, " ".join(emp.validate_manifest(manifest)), needle)
        self.assertEqual(emp.validate_manifest([]), ["manifest is not a JSON object"])

    def test_manifest_with_unknown_skill_or_long_charter_fails(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "review").mkdir()
        (tmp / "review" / "hat.md").write_text("word " * 151)
        manifest = {**emp.load_manifest("review"), "skills": ["no-such-skill"]}
        self.assertIn("no SKILL.md", " ".join(emp.validate_manifest(manifest, tmp / "review")))
        manifest["skills"] = ["finish"]
        self.assertIn("over 150 words", " ".join(emp.validate_manifest(manifest, tmp / "review")))
        self.assertIn("hat.md missing", " ".join(emp.validate_manifest(manifest, tmp / "nothing")))

    def test_each_listed_skill_loads_with_skill_show(self):
        for hat in emp.hat_names():
            for skill in emp.load_manifest(hat)["skills"]:
                code, out, _ = run("skill", "show", skill, "--hat", hat)
                self.assertEqual(code, 0, f"{hat}/{skill}")
                self.assertTrue(out.startswith(f"---\nname: {skill}\ndescription: "), f"{hat}/{skill}")

    def test_hats_command_and_the_charter(self):
        self.assertEqual(run("hats"), (0, "build: ok\nqa: ok\nreview: ok\n", ""))
        self.assertIn("# Hat: review", emp.hat_charter("review"))

    def test_no_template_names_a_person_or_a_memory(self):
        words = " ".join(p.read_text().lower() for p in emp.HATS.rglob("*") if p.is_file())
        words += " ".join(p.read_text().lower() for p in emp.SHARED_SKILLS.rglob("*") if p.is_file())
        for gone in ("memory", "remember", "roster", "employees/", "write-back"):
            self.assertNotIn(gone, words, gone)


class DbTests(ProfileCase):
    def test_ensure_schema_works_before_and_after_sessions_exist(self):
        conn = sqlite3.connect(":memory:")
        hwlib.ensure_session_schema(conn)           # a DB with no sessions table yet: no error
        conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")
        hwlib.ensure_session_schema(conn)
        hwlib.ensure_session_schema(conn)           # idempotent
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        self.assertTrue({"hat", "verdict", "verdict_tree", "core_start", "plan"} <= cols)
        self.assertFalse({"employee", "verdict_sha", "origin"} & cols)  # no new column for the removed identity

    def test_answers_given_before_they_were_tracked_count_as_given_and_an_answer_is_taken_once(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("CREATE TABLE sessions (id INTEGER PRIMARY KEY);"
                           "CREATE TABLE questions (id INTEGER PRIMARY KEY, session_id INTEGER, question TEXT, answer TEXT, status TEXT, answered_at TEXT);"
                           "INSERT INTO questions VALUES (1, 7, 'Old?', 'yes', 'answered', '2026-01-01 10:00'), (2, 7, 'Open?', NULL, 'open', NULL);")
        hwlib.ensure_session_schema(conn)
        hwlib.ensure_session_schema(conn)  # idempotent
        self.assertEqual([tuple(r) for r in conn.execute("SELECT id, delivered_at FROM questions")], [(1, "2026-01-01 10:00"), (2, None)])
        self.assertEqual(hwlib.take_answers(conn, 7, "now"), [])  # an old answer is not sent again
        conn.execute("UPDATE questions SET answer='no', status='answered' WHERE id=2")
        self.assertEqual(hwlib.take_answers(conn, 8, "now"), [])  # another session's
        taken, = hwlib.take_answers(conn, 7, "now")
        self.assertEqual(hwlib.answer_line(taken), "Answer from the user to your question Q2 (Open?): no")
        self.assertEqual(hwlib.take_answers(conn, 7, "later"), [])  # once

    def test_a_database_from_before_hats_lost_their_identities_keeps_working(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript("CREATE TABLE sessions (id INTEGER PRIMARY KEY, employee TEXT, verdict_sha TEXT, origin TEXT);"
                           "CREATE TABLE employees (name TEXT); CREATE TABLE queue (id INTEGER PRIMARY KEY, employee TEXT NOT NULL);"
                           "INSERT INTO sessions(id, employee, verdict_sha) VALUES (1, 'juno', 'abc');")
        hwlib.ensure_session_schema(conn)
        row = conn.execute("SELECT employee, verdict_sha, hat, verdict_tree FROM sessions").fetchone()
        self.assertEqual(row, ("juno", "abc", None, None))  # the old columns stay, unused; the old verdict matches no tree


class OldDatabaseTests(unittest.TestCase):
    """A profile from before the rename to Herdwright still calls its database `zed.db` until `bin/hw start` renames it."""

    def test_a_worker_command_does_not_create_an_empty_database_in_front_of_the_old_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "zed.db", Path(tmp) / "hw.db"
            old.write_bytes(b"")
            with patch.object(hwlib, "OLD_DB_PATH", old), patch.object(hwlib, "DB_PATH", new):
                with self.assertRaises(SystemExit) as refused:
                    emp.connect()
                self.assertEqual(refused.exception.code, f"hw-employee: this profile is from before the rename to Herdwright: its database "
                                                         f"is still {old}. Run `bin/hw start` first: it renames the profile's old names once.")
                self.assertFalse(new.exists())
                old.rename(new)  # what `start` does
                emp.connect().close()


if __name__ == "__main__":
    unittest.main()
