"""The rename from Zed to Herdwright: the assistant's name comes from the profile, and a profile from before the
rename keeps working (`start` renames what still carries the old name, once, and says so).

The old names appear here on purpose: they are what the migration looks for."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from _e2e import HOME, LoopCase, clean_env

BEFORE_RENAME = "c80d704"  # the last commit whose code is called Zed: `bin/zed`, `ZED_PROFILE_DIR`, `db/zed.db`


class AssistantName(LoopCase):
    def settings(self):
        return json.loads((self.w.profile / "config" / "settings.json").read_text())

    def test_without_a_name_in_the_profile_start_shows_the_default(self):
        self.assertNotIn("assistant_name", self.settings())
        self.assertIn("assistant name: Herdwright", self.w.start().stdout.splitlines())
        self.assertEqual(self.w.hw("name").stdout, "Herdwright\n")

    def test_start_shows_the_name_stored_in_the_profile_and_the_other_settings_stay(self):
        before = self.settings()
        self.assertEqual(self.w.hw("name", "--set", "Ada", "Lovelace").stdout, "Ada Lovelace\n")
        self.assertEqual(self.settings(), before | {"assistant_name": "Ada Lovelace"})
        self.assertIn("assistant name: Ada Lovelace", self.w.start().stdout.splitlines())

    def test_set_needs_a_name_and_ask_asks_nothing_outside_a_terminal(self):
        r = self.w.hw("name", "--set", " ", check=False)
        self.assertEqual((r.returncode, r.stderr), (1, "hw: usage: hw name --set <name>\n"))
        r = self.w.hw("name", "--ask")  # stdin is not a terminal here
        self.assertEqual((r.stdout, r.stderr), ("Herdwright\n", ""))
        self.assertNotIn("assistant_name", self.settings())


class OldProfile(LoopCase):
    """A profile that still carries the old names, made by hand."""

    def setUp(self):
        super().setUp()
        self.w.task("Made before the rename")  # creates db/hw.db
        self.w.start()                          # takes the lock: .cache/hw.lock
        self.db, self.cache = self.w.profile / "db", self.w.profile / ".cache"
        for name in ("hw.db", "hw.db-wal", "hw.db-shm"):
            if (self.db / name).exists():
                (self.db / name).rename(self.db / name.replace("hw", "zed"))
        (self.cache / "hw.lock").rename(self.cache / "zed.lock")
        self.plugins = [self.cache / "coordinator-opencode" / "plugins" / "zed-pretool",
                        self.cache / "hats" / "build-t1-opencode" / "plugins" / "zed-deny"]
        for folder in self.plugins:
            folder.mkdir(parents=True)
            (folder / "index.js").write_text("// written by the old code\n")

    def test_start_renames_the_old_names_once_and_says_so(self):
        out = self.w.start().stdout
        for old, new in ((self.db / "zed.db", "hw.db"), (self.cache / "zed.lock", "hw.lock"),
                         (self.plugins[0], "hw-pretool"), (self.plugins[1], "hw-deny")):
            self.assertIn(f"renamed {old} to {new} (a profile from before the rename to Herdwright)", out)
            self.assertFalse(old.exists(), old)
            self.assertTrue(old.with_name(new).exists(), new)
        self.assertIn("lock: held by this session", out)  # the old lock was this session's: it still holds it
        self.assertIn("Made before the rename", self.w.hw("task", "list").stdout)
        again = self.w.start().stdout
        self.assertNotIn("renamed", again)
        self.assertEqual(sorted(p.name for p in self.db.iterdir() if p.name.startswith(("hw", "zed"))),
                         sorted(p.name for p in self.db.iterdir() if p.name.startswith("hw")))

    def test_an_old_name_next_to_its_new_one_is_left_alone_and_reported(self):
        (self.db / "hw.db").write_bytes(b"")
        out = self.w.start().stdout
        self.assertIn(f"OLD NAME LEFT ALONE: {self.db / 'zed.db'} and {self.db / 'hw.db'} both exist", out)
        self.assertTrue((self.db / "zed.db").exists())
        self.assertNotIn("Made before the rename", self.w.hw("task", "list").stdout)  # the new name is the one in use

    def test_an_old_variable_is_not_read_and_start_names_it(self):
        elsewhere = self.w.root / "elsewhere"
        r = subprocess.run([sys.executable, str(HOME / "bin" / "hw"), "start"], capture_output=True, text=True, cwd=self.w.root,
                           env=self.w.env() | {"ZED_PROFILE_DIR": str(elsewhere)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("NOT READ: ZED_PROFILE_DIR (the old names). Set HW_PROFILE_DIR instead.", r.stdout)
        self.assertIn(f"profile {self.w.profile}", r.stdout)
        self.assertFalse(elsewhere.exists())


class OldSyncRef(unittest.TestCase):
    """hw-core-sync's sync point lives in the checkout, not in the profile: a throwaway checkout stands in for it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.env = clean_env(self.root)
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "Start")
        self.head = self.git("rev-parse", "HEAD")
        self.z = types.ModuleType("hw")
        self.z.__file__ = str(HOME / "bin" / "hw")
        with patch.dict(os.environ, {"HW_PROFILE_DIR": str(self.root / "profile")}):
            exec(compile((HOME / "bin" / "hw").read_text(), self.z.__file__, "exec"), self.z.__dict__)
        self.z.HW_HOME = self.root
        self.enterContext(patch.dict(os.environ, self.env, clear=True))

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, env=self.env, capture_output=True, text=True, check=True).stdout.strip()

    def test_the_old_sync_ref_moves_to_its_new_name_once(self):
        self.git("update-ref", "refs/zed/core-sync/profile/ada", self.head)
        self.assertEqual(self.z.migrate_old_names(), ["renamed git ref refs/zed/core-sync/profile/ada to refs/hw/core-sync/profile/ada"])
        self.assertEqual(self.git("for-each-ref", "--format=%(refname) %(objectname)"),
                         f"refs/heads/main {self.head}\nrefs/hw/core-sync/profile/ada {self.head}")
        self.assertEqual(self.z.migrate_old_names(), [])


@unittest.skipUnless(subprocess.run(["git", "-C", str(HOME), "cat-file", "-e", f"{BEFORE_RENAME}^{{commit}}"], capture_output=True).returncode == 0,
                     f"needs commit {BEFORE_RENAME} (the code before the rename) in this checkout's history")
class ProfileMadeByTheOldCode(LoopCase):
    """The profile is created by the code as it was before the rename, checked out into a scratch folder."""

    def old(self, *args):
        r = subprocess.run([sys.executable, str(self.old_home / "bin" / "zed"), *args], capture_output=True, text=True, cwd=self.w.root,
                           env=self.w.env() | {"ZED_PROFILE_DIR": str(self.w.profile)})
        self.assertEqual(r.returncode, 0, f"bin/zed {' '.join(args)}\n{r.stdout}{r.stderr}")
        return r.stdout

    def test_what_the_old_code_recorded_is_there_after_the_first_start_of_the_new_code(self):
        self.old_home = self.w.root / "before-rename"
        self.old_home.mkdir()
        (self.w.profile / ".cache" / "hw.lock").unlink()  # the world's coordinator took it with the new code
        archive = subprocess.run(["git", "-C", str(HOME), "archive", BEFORE_RENAME, "bin", "templates", ".claude"], capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(self.old_home)], input=archive.stdout, check=True)
        self.old("lock", "acquire")
        tid = self.old("task", "add", "title=Recorded by the old code").strip()
        self.old("q", "add", tid, "Which colour?")
        self.assertEqual(sorted(p.name for p in (self.w.profile / "db").iterdir() if p.suffix == ".db"), ["zed.db"])
        self.assertTrue((self.w.profile / ".cache" / "zed.lock").exists())

        out = self.w.start().stdout
        self.assertIn(f"renamed {self.w.profile / 'db' / 'zed.db'} to hw.db", out)
        self.assertIn(f"renamed {self.w.profile / '.cache' / 'zed.lock'} to hw.lock", out)
        self.assertIn("lock: held by this session", out)
        self.assertIn("Which colour?", out)  # the open question the old code recorded is in the digest
        self.assertIn("Recorded by the old code", self.w.hw("task", "list").stdout)
        self.assertEqual(sorted(p.name for p in (self.w.profile / "db").iterdir() if p.suffix == ".db"), ["hw.db"])
        self.assertNotIn("renamed", self.w.start().stdout)


if __name__ == "__main__":
    unittest.main()
