import contextlib
import io
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _load

import zedemployee as emp
import zedlib
import zedmem

cli = _load.load("zed-employee")
SIX = ["atlas", "forge", "juno", "pip", "quill", "scout"]


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
            zedlib.git(self.repo, *cmd)
        (self.repo / "guard.py").write_text("def require_role(user): ...\n")
        zedlib.git(self.repo, "add", ".")
        zedlib.git(self.repo, "commit", "-qm", "init")
        (self.profile / "config" / "projects.json").write_text(
            json.dumps({"repos": {"api": {"path": str(self.repo), "dev_base": "main"}}}))
        for name, value in (("PROFILE_DIR", self.profile), ("DB_PATH", self.profile / "db" / "zed.db"),
                            ("PROJECTS_PATH", self.profile / "config" / "projects.json")):
            patcher = patch.object(zedlib, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)


class TemplateTests(unittest.TestCase):
    def test_every_manifest_validates(self):
        self.assertEqual(emp.hat_names(), ["build", "qa", "review"])
        for hat in emp.hat_names():
            self.assertEqual(emp.validate_manifest(emp.load_manifest(hat), emp.TEMPLATES / "hats" / hat), [], hat)

    def test_manifest_schema_refusals(self):
        good = emp.load_manifest("review")
        cases = {
            "scripts": {**good, "scripts": "zed-pr"},
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
                fields, body = emp.parse_frontmatter(out)
                self.assertEqual(fields["name"], skill)
                self.assertTrue(fields["description"] and body)

    def test_roster_has_the_six_and_only_atlas_and_forge_are_senior(self):
        self.assertEqual(emp.roster_names(), SIX)
        for n in SIX:
            f, body = emp.parse_frontmatter((emp.TEMPLATES / "roster" / f"{n}.md").read_text())
            self.assertEqual(f["name"], n)
            self.assertTrue(f["voice"] and f["strengths"] and body)
            self.assertEqual(f["senior"], n in ("atlas", "forge"))

    def test_employee_rules_names_its_placeholders(self):
        text = (emp.TEMPLATES / "employee-rules.md").read_text()
        self.assertEqual(sorted(set(re.findall(r"\{\{(\w+)\}\}", text))), ["HAT", "NAME", "PACK", "VOICE"])


class FrontmatterTests(unittest.TestCase):
    def test_roundtrip(self):
        fields = {"name": "x", "voice": 'Kind: "yes"', "strengths": ["a", "b"], "senior": False, "memory_words": 1500}
        parsed, body = emp.parse_frontmatter(emp.render_frontmatter(fields) + "# X\n")
        self.assertEqual(parsed, fields)
        self.assertEqual(body, "# X")

    def test_list_items_with_commas_and_brackets_roundtrip(self):
        fields = {"strengths": ["fast, careful", "uses [x]", 'says "no"', "plain", "# tag", "a # b", "a #b"], "none": []}
        parsed, _ = emp.parse_frontmatter(emp.render_frontmatter(fields) + "# X\n")
        self.assertEqual(parsed, fields)

    def test_comments_and_missing_block(self):
        self.assertEqual(emp.parse_frontmatter("---\nruntime: auto   # the profile order\n---\n")[0], {"runtime": "auto"})
        self.assertEqual(emp.parse_frontmatter('---\nk: "x # y" # c\n---\n')[0], {"k": "x # y"})
        self.assertEqual(emp.parse_frontmatter("no block"), ({}, "no block"))


class HireTests(ProfileCase):
    def test_hire_juno_and_quill_and_print_intro_cards_from_files(self):
        for n in ("juno", "quill"):
            self.assertEqual(run("hire", n)[0], 0)
        edir = self.profile / "employees" / "quill"
        fields, body = emp.parse_frontmatter((edir / "employee.md").read_text())
        self.assertEqual((fields["name"], fields["senior"], fields["runtime"], fields["memory_words"]),
                         ("quill", False, "auto", 1500))
        self.assertNotIn("role", fields)
        self.assertIn("Quill reads for the person who maintains this next year", body)
        for hat in ("general", "build", "review", "qa"):
            self.assertTrue((edir / "memory" / "craft" / f"{hat}.md").exists(), hat)
        self.assertTrue((edir / "log.md").exists())
        code, out, _ = run("intro", "quill", "--record")
        self.assertEqual(code, 0)
        for needle in ("Quill", "Kind but exacting", "readability", "build, qa, review", "Never without approval", "Remembers"):
            self.assertIn(needle, out)
        _, listing, _ = run("list")
        self.assertIn("juno", listing)
        self.assertRegex(listing, r"quill\s+hired \S+\s+memory 0\s+introduced \d{4}")
        self.assertIn("not introduced", listing.splitlines()[0])
        rows = sqlite3.connect(zedlib.DB_PATH).execute("SELECT name, senior FROM employees ORDER BY name").fetchall()
        self.assertEqual(rows, [("juno", 0), ("quill", 0)])

    def test_hire_refusals(self):
        run("hire", "juno")
        self.assertIn("already hired", run("hire", "juno")[2])
        self.assertIn("not in the roster", run("hire", "newbie")[2])
        self.assertIn("lowercase", run("hire", "Bad Name")[2])
        self.assertEqual(run("hire", "newbie", "--voice", "Dry.", "--strengths", "x,y", "--senior")[0], 0)
        fields, _ = emp.load_employee("newbie")
        self.assertEqual((fields["senior"], fields["strengths"]), (True, ["x", "y"]))

    def test_senior_comes_from_the_card_unless_overridden(self):
        run("hire", "atlas")
        run("hire", "forge", "--no-senior")
        self.assertTrue(emp.load_employee("atlas")[0]["senior"])
        self.assertFalse(emp.load_employee("forge")[0]["senior"])

    def test_show_and_unknown_employee(self):
        run("hire", "juno")
        code, out, _ = run("show", "juno")
        self.assertIn("name: juno", out)
        self.assertIn("memory: fact 0, preference 0", out)
        code, _, err = run("show", "ghost")
        self.assertEqual(code, 2)
        self.assertIn("no employee 'ghost'", err)

    def test_employee_own_skill_wins_over_the_templates(self):
        run("hire", "juno")
        own = self.profile / "employees" / "juno" / "skills" / "finish"
        own.mkdir(parents=True)
        (own / "SKILL.md").write_text("---\nname: finish\n---\nJuno's own version\n")
        self.assertEqual(emp.skill_path("finish", "juno"), own / "SKILL.md")
        self.assertNotEqual(emp.skill_path("finish", "quill"), own / "SKILL.md")


class MemoryCliTests(ProfileCase):
    def setUp(self):
        super().setUp()
        run("hire", "quill")

    def test_remember_pack_verify_end_to_end(self):
        code, out, _ = run("remember", "-e", "quill", "--kind", "fact", "--repo", "api", "--src", "guard.py",
                           "--anchor", "def require_role", "--tags", "auth", "Role checks live in guard.py.")
        self.assertEqual((code, out.strip()), (0, "remembered m-0001"))
        run("remember", "-e", "quill", "--kind", "lesson", "--hat", "review", "--src", "T17 S31", "Name the fix.")
        code, text, _ = run("pack", "-e", "quill", "--repo", "api", "--hat", "review", "--tags", "auth", "--dry-run")
        self.assertIn("Role checks live in guard.py.", text)
        self.assertIn("Name the fix.", text)
        code, out, _ = run("pack", "-e", "quill", "--repo", "api", "--hat", "review", "--tags", "auth",
                         "--session", "S42")
        packfile = self.profile / ".cache" / "employees" / "quill" / "S42-pack.md"
        self.assertEqual(packfile.read_text().strip(), text.strip())
        self.assertIn("2 entries", out)
        self.assertEqual(zedmem.find(self.profile / "employees" / "quill", "m-0001")[1].uses, 1)
        # the anchor disappears from the base branch: verify says stale, the next pack lists it for attention
        (self.repo / "guard.py").write_text("renamed\n")
        zedlib.git(self.repo, "commit", "-qam", "rename")
        code, out, _ = run("verify", "-e", "quill", "--repo", "api")
        self.assertEqual(code, 1)
        self.assertIn("stale 1 (m-0001)", out)
        self.assertIn("## Needs your attention", run("pack", "-e", "quill", "--repo", "api", "--hat", "build", "--dry-run")[1])
        # the employee refuses to refresh without a real anchor, then drops with a reason
        self.assertEqual(run("refresh", "-e", "quill", "m-0001", "--anchor", "def require_role")[0], 1)
        self.assertEqual(run("drop", "-e", "quill", "m-0001", "--reason", "renamed")[0], 0)
        self.assertEqual(run("verify", "-e", "quill", "--repo", "api")[0], 0)

    def test_refusals_exit_1_with_the_reason_and_unknown_things_exit_2(self):
        code, _, err = run("remember", "-e", "quill", "--kind", "preference", "--repo", "api", "Small PRs.")
        self.assertEqual(code, 1)
        self.assertIn("src is mandatory", err)
        code, _, err = run("remember", "-e", "quill", "--kind", "fact", "--repo", "nope", "--src", "a.py", "--anchor", "a", "x")
        self.assertEqual(code, 1)
        self.assertIn("not in config/projects.json", err)
        self.assertEqual(run("pack", "-e", "quill", "--repo", "api", "--hat", "wizard")[0], 2)
        self.assertEqual(run("remember", "--kind", "lesson", "--src", "T1", "x")[0], 2)  # no employee given

    def test_employee_defaults_to_zed_employee_env(self):
        with patch.dict("os.environ", {"ZED_EMPLOYEE": "quill"}):
            self.assertEqual(run("remember", "--kind", "lesson", "--src", "T1", "Read twice.")[0], 0)
        self.assertEqual(len(zedmem.all_entries(self.profile / "employees" / "quill")), 1)

    def test_pack_never_marks_facts_stale_when_the_repo_is_unreadable(self):
        run("remember", "-e", "quill", "--kind", "fact", "--repo", "api", "--src", "guard.py", "--anchor", "def require_role", "x")
        zedlib.PROJECTS_PATH.write_text(json.dumps({"repos": {}}))
        code, _, err = run("pack", "-e", "quill", "--repo", "api", "--hat", "build", "--dry-run")
        self.assertEqual(code, 1)
        self.assertEqual(zedmem.find(self.profile / "employees" / "quill", "m-0001")[1].get("stale"), None)


class DbTests(ProfileCase):
    def test_ensure_schema_works_before_and_after_sessions_exist(self):
        conn = sqlite3.connect(":memory:")
        zedlib.ensure_employee_schema(conn)           # a DB with no sessions table yet: no error
        conn.execute("CREATE TABLE sessions (id INTEGER PRIMARY KEY)")
        zedlib.ensure_employee_schema(conn)
        zedlib.ensure_employee_schema(conn)           # idempotent
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        self.assertTrue({"employee", "hat", "verdict", "origin", "direct"} <= cols)


if __name__ == "__main__":
    unittest.main()
