import tempfile
import unittest
from pathlib import Path

import _load  # noqa: F401  (puts bin/lib on sys.path)
import zedlib
import zedmem as mem

FILES = {"app/guard.py": "def require_role(user):\n    ...\n"}


def reader(repo, path):
    return FILES.get(path) if repo == "api" else None


class MemoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.edir = Path(tmp.name)

    def fact(self, text="Role checks live in guard.py.", **kw):
        kw.setdefault("anchor", "def require_role")
        return mem.remember(self.edir, "fact", text, kw.pop("src", "app/guard.py"), repo="api",
                            repo_reader=reader, today=kw.pop("today", "2026-10-20"), **kw)

    def refused(self, *args, **kw):
        with self.assertRaises(mem.MemoryRefused) as cm:
            mem.remember(*args, **kw)
        return str(cm.exception)

    # -- format
    def test_roundtrip_and_stamped_fields(self):
        e = self.fact(tags=["auth", "views"])
        self.assertEqual((e.id, e.date, e.get("verified"), e.get("ttl"), e.get("uses")),
                         ("m-0001", "2026-10-20", "2026-10-20", "60d", "0"))
        text = (self.edir / "memory/repos/api.md").read_text()
        self.assertIn('- [m-0001] fact | 2026-10-20 | tags: auth,views | src: app/guard.py anchor: "def require_role"'
                      ' | verified: 2026-10-20 | ttl: 60d | uses: 0\n  Role checks live in guard.py.', text)
        pre, entries = mem.parse_entries(text)
        self.assertEqual(mem.render_entries(pre, entries), text)
        self.assertEqual((entries[0].src_path, entries[0].anchor, entries[0].tags),
                         ("app/guard.py", "def require_role", ["auth", "views"]))

    def test_anchor_with_pipe_roundtrips(self):
        FILES["app/x.py"] = "a || b"
        self.addCleanup(FILES.pop, "app/x.py")
        e = mem.remember(self.edir, "fact", "x", "app/x.py", repo="api", anchor="a || b", repo_reader=reader)
        _, parsed = mem.parse_entries((self.edir / "memory/repos/api.md").read_text())
        self.assertEqual(parsed[0].anchor, "a || b")
        self.assertEqual(parsed[0].id, e.id)

    def test_kinds_ttl_and_files(self):
        pref = mem.remember(self.edir, "preference", "Approve only without blockers.", "decision T17 E41",
                            repo="api", today="2026-10-22")
        lesson = mem.remember(self.edir, "lesson", "Name the fix.", "T17 S31", hat="review")
        general = mem.remember(self.edir, "lesson", "Read the diff twice.", "T17 S31")
        heard = mem.remember(self.edir, "heard", "Deploy is on Fridays.", "msg #12", repo="api")
        work = mem.remember(self.edir, "work", "Reviewed PR 512.", "T17 S31 PR#512", repo="api", hat="review")
        self.assertEqual([e.get("ttl") for e in (pref, lesson, general, heard, work)],
                         ["180d", None, None, "30d", "180d"])
        self.assertEqual(work.get("hat"), "review")
        self.assertTrue((self.edir / "memory/craft/review.md").exists())
        self.assertTrue((self.edir / "memory/craft/general.md").exists())
        self.assertEqual(mem.next_id(self.edir), "m-0006")

    # -- one test per refusal
    def test_refuses_missing_src(self):
        self.assertIn("src is mandatory", self.refused(self.edir, "preference", "x", "", repo="api"))

    def test_refuses_fact_without_anchor_or_with_absent_anchor(self):
        self.assertIn("needs --anchor", self.refused(self.edir, "fact", "x", "app/guard.py", repo="api",
                                                      repo_reader=reader))
        self.assertIn("not found", self.refused(self.edir, "fact", "x", "app/guard.py", repo="api",
                                                anchor="def nothing_here", repo_reader=reader))
        self.assertIn("not found", self.refused(self.edir, "fact", "x", "app/missing.py", repo="api",
                                                anchor="a", repo_reader=reader))

    def test_url_fact_is_manual_check(self):
        e = mem.remember(self.edir, "fact", "Runbook page.", "https://wiki.example/x", repo="api")
        self.assertEqual(e.get("check"), "manual")

    def test_refuses_over_120_words(self):
        self.assertIn("121 words", self.refused(self.edir, "preference", "word " * 121, "decision T1", repo="api"))
        mem.remember(self.edir, "preference", "word " * 120, "decision T1", repo="api")

    def test_refuses_secrets_of_any_kind(self):
        for kind, src in (("preference", "decision T1"), ("lesson", "T1"), ("heard", "msg #1")):
            msg = self.refused(self.edir, kind, "the password: hunter2hunter2", src, repo="api")
            self.assertIn("secret", msg)

    def test_refuses_duplicate_ignoring_case_and_spacing(self):
        mem.remember(self.edir, "preference", "Use small PRs.", "decision T1", repo="api")
        self.assertIn("duplicates m-0001",
                      self.refused(self.edir, "lesson", "use  small   prs", "T2"))

    def test_refuses_unknown_kind_and_misplaced_args(self):
        self.assertIn("unknown kind", self.refused(self.edir, "note", "x", "s", repo="api"))
        self.assertIn("needs --repo", self.refused(self.edir, "preference", "x", "decision T1"))
        self.assertIn("heard entry's src", self.refused(self.edir, "heard", "x", "T5", repo="api"))
        self.assertIn("only for facts", self.refused(self.edir, "preference", "x", "decision T1", repo="api", anchor="a"))

    def test_conflict_is_flagged_not_refused(self):
        self.fact("Role checks live in guard.py.")
        e = self.fact("Role checks moved to middleware.")
        self.assertEqual(e.get("conflicts"), "m-0001")

    # -- refresh / drop
    def test_drop_archives_with_reason_and_needs_one(self):
        self.fact()
        with self.assertRaises(mem.MemoryRefused):
            mem.drop(self.edir, "m-0001", " ")
        mem.drop(self.edir, "m-0001", "wrong", today="2026-11-01")
        self.assertEqual(mem.all_entries(self.edir), [])
        self.assertIn("dropped: 2026-11-01: wrong", (self.edir / "memory/archive.md").read_text())
        self.assertEqual(mem.next_id(self.edir), "m-0002")  # ids are never reused

    def test_refresh_restamps_and_rechecks_the_anchor(self):
        self.fact()
        e = mem.refresh(self.edir, "m-0001", repo_reader=reader, today="2026-12-01")
        self.assertEqual(e.get("verified"), "2026-12-01")
        FILES["app/guard.py"] = "gone"
        self.addCleanup(FILES.update, {"app/guard.py": "def require_role(user):\n"})
        with self.assertRaises(mem.MemoryRefused):
            mem.refresh(self.edir, "m-0001", repo_reader=reader)

    def test_refresh_upgrades_heard_to_fact(self):
        mem.remember(self.edir, "heard", "Guard is in guard.py.", "msg #3", repo="api", today="2026-10-01")
        with self.assertRaises(mem.MemoryRefused):
            mem.refresh(self.edir, "m-0001")
        e = mem.refresh(self.edir, "m-0001", src="app/guard.py", anchor="def require_role",
                        repo_reader=reader, today="2026-10-02")
        self.assertEqual((e.kind, e.get("ttl"), e.get("verified")), ("fact", "60d", "2026-10-02"))

    # -- verify
    def test_verify_marks_stale_expired_and_archives_old_work(self):
        self.fact()                                            # m-0001 anchored fact
        mem.remember(self.edir, "preference", "Small PRs.", "decision T1", repo="api", today="2026-01-01")
        mem.remember(self.edir, "work", "Did a thing.", "T1 S1", repo="api", hat="build", today="2026-01-01")
        report = mem.verify(self.edir, "api", repo_reader=reader, today="2026-10-25")
        self.assertEqual(report, {"ok": ["m-0001"], "stale": [], "expired": ["m-0002"], "archived": ["m-0003"]})
        FILES["app/guard.py"] = "renamed"
        self.addCleanup(FILES.update, {"app/guard.py": "def require_role(user):\n"})
        report = mem.verify(self.edir, "api", repo_reader=reader, today="2026-10-26")
        self.assertEqual(report["stale"], ["m-0001"])
        _, entries = mem.load(self.edir / "memory/repos/api.md")
        self.assertEqual(entries[0].get("stale"), "2026-10-26")

    # -- pack
    def seed(self):
        self.fact("Role checks live in guard.py.", tags=["auth"], today="2026-10-20")
        mem.remember(self.edir, "preference", "Approve only without blockers.", "decision T17", repo="api",
                     tags=["prs"], today="2026-10-22")
        mem.remember(self.edir, "work", "Reviewed PR 512.", "T17 S31", repo="api", hat="review", tags=["auth"],
                     today="2026-10-26")
        mem.remember(self.edir, "lesson", "Name the fix.", "T17 S31", hat="review")
        mem.remember(self.edir, "lesson", "Build lesson.", "T17 S31", hat="build")
        mem.remember(self.edir, "lesson", "Read the diff twice.", "T17 S31")

    def test_pack_is_byte_identical_and_hat_selected(self):
        self.seed()
        a = mem.build_pack(self.edir, "quill", repo="api", hat="review", tags=["auth"], today="2026-10-27")
        b = mem.build_pack(self.edir, "quill", repo="api", hat="review", tags=["auth"], today="2026-10-27")
        self.assertEqual(a, b)
        text, used, attention = a
        self.assertIn("Name the fix.", text)
        self.assertIn("Read the diff twice.", text)
        self.assertNotIn("Build lesson.", text)
        self.assertNotIn("Approve only without blockers.", text)  # tag prs does not intersect auth
        self.assertEqual((attention, sorted(used)), ([], ["m-0001", "m-0003", "m-0004", "m-0006"]))
        self.assertLess(text.index("Reviewed PR 512."), text.index("Role checks"))  # newest first

    def test_pack_without_tags_takes_all_repo_entries(self):
        self.seed()
        text, used, _ = mem.build_pack(self.edir, "quill", repo="api", hat="build", today="2026-10-27")
        self.assertIn("Approve only without blockers.", text)
        self.assertIn("Build lesson.", text)
        self.assertNotIn("Name the fix.", text)

    def test_pack_budget_stops_at_memory_words(self):
        self.seed()
        text, used, _ = mem.build_pack(self.edir, "quill", repo="api", hat="build", memory_words=4, today="2026-10-27")
        self.assertIn("Reviewed PR 512.", text)  # 3 words, newest
        self.assertNotIn("Role checks", text)

    def test_pack_caps_work_at_150_words(self):
        for i in range(3):
            mem.remember(self.edir, "work", f"item {i} " + "w " * 70, f"T{i}", repo="api", hat="build",
                         today="2026-10-2%d" % i)
        text, used, _ = mem.build_pack(self.edir, "quill", repo="api", hat="build", today="2026-10-27")
        self.assertEqual(len(used), 2)  # 72 + 72 <= 150 < 216

    def test_pack_marks_expired_unverified_caps_five_and_lists_stale_separately(self):
        for i in range(7):
            mem.remember(self.edir, "preference", f"Preference number {i}.", f"decision T{i}", repo="api",
                         today="2026-01-01")
        self.fact(tags=["auth"], today="2026-10-20")
        e = self.fact("Stale thing.", tags=["auth"], today="2026-10-20")
        FILES["app/guard.py"] = "renamed"
        self.addCleanup(FILES.update, {"app/guard.py": "def require_role(user):\n"})
        mem.verify(self.edir, "api", repo_reader=reader, today="2026-10-26")
        text, used, attention = mem.build_pack(self.edir, "q", repo="api", hat="build", today="2026-10-27")
        self.assertEqual(text.count("UNVERIFIED"), 5)
        self.assertEqual(attention, ["m-0008", "m-0009"])
        self.assertIn("## Needs your attention", text)
        self.assertNotIn("m-0008", [u for u in used])

    def test_record_pack_counts_uses_and_archives_after_two_unresolved_packs(self):
        self.seed()
        _, used, _ = mem.build_pack(self.edir, "q", repo="api", hat="review", today="2026-10-27")
        mem.record_pack(self.edir, "api", used, [])
        self.assertEqual(mem.find(self.edir, "m-0001")[1].uses, 1)
        self.assertEqual(mem.find(self.edir, "m-0004")[1].uses, 1)
        FILES["app/guard.py"] = "renamed"
        self.addCleanup(FILES.update, {"app/guard.py": "def require_role(user):\n"})
        mem.verify(self.edir, "api", repo_reader=reader, today="2026-10-27")
        self.assertEqual(mem.record_pack(self.edir, "api", [], ["m-0001"]), [])
        self.assertEqual(mem.find(self.edir, "m-0001")[1].get("unresolved"), "1")
        self.assertEqual(mem.record_pack(self.edir, "api", [], ["m-0001"]), ["m-0001"])
        self.assertIn("unresolved", (self.edir / "memory/archive.md").read_text())


class GitReaderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            zedlib.git(self.repo, *cmd)
        (self.repo / "a.py").write_text("def present(): pass\n")
        zedlib.git(self.repo, "add", ".")
        zedlib.git(self.repo, "commit", "-qm", "init")
        self.read = mem.git_reader(lambda: {"api": {"path": str(self.repo), "dev_base": "main"},
                                            "nobase": {"path": str(self.repo), "dev_base": "nope"}})

    def test_reads_file_on_base_and_none_when_absent(self):
        self.assertIn("def present", self.read("api", "a.py"))
        self.assertIsNone(self.read("api", "missing.py"))

    def test_unreadable_repo_raises_instead_of_looking_stale(self):
        for repo in ("unknown", "nobase"):
            with self.assertRaises(mem.RepoUnavailable):
                self.read(repo, "a.py")
        with self.assertRaises(mem.MemoryRefused):  # remember turns it into a refusal the employee can read
            mem.remember(Path(tempfile.mkdtemp()), "fact", "x", "a.py", repo="unknown", anchor="a", repo_reader=self.read)


if __name__ == "__main__":
    unittest.main()
