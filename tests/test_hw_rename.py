"""The rename from Zed to Herdwright: the assistant's name comes from the profile, and a profile from before the
rename keeps working (`start` renames what still carries the old name, once, and says so).

The old names appear here on purpose: they are what the migration looks for."""
import json
import shutil
import subprocess
import sys
import unittest

from _e2e import HOME, LoopCase

BEFORE_RENAME = "c80d704"  # the last commit whose code is called Zed: `bin/zed`, `ZED_PROFILE_DIR`, `db/zed.db`


def refs(checkout):
    """Every ref of a checkout with what it points at."""
    return subprocess.run(["git", "-C", str(checkout), "for-each-ref", "--format=%(refname) %(objectname)"],
                          capture_output=True, text=True, check=True).stdout.splitlines()


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

    def test_a_name_is_stored_on_one_line(self):
        self.assertEqual(self.w.hw("name", "--set", "Ada\nLovelace  the\tFirst").stdout, "Ada Lovelace the First\n")
        self.assertIn("assistant name: Ada Lovelace the First", self.w.start().stdout.splitlines())


class OldProfile(LoopCase):
    """A profile that still carries the old names, made by hand."""

    def setUp(self):
        super().setUp()
        self.w.task("Made before the rename")  # creates db/hw.db
        self.w.start()                          # takes the lock: .cache/hw.lock
        self.db, self.cache = self.w.profile / "db", self.w.profile / ".cache"
        (self.db / "hw.db").rename(self.db / "zed.db")
        (self.cache / "hw.lock").rename(self.cache / "zed.lock")
        (self.cache / "hw.guard").rename(self.cache / "zed.guard")
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
        self.assertIn(f"removed {self.cache / 'zed.guard'} (the old lock's empty guard file)", out)
        self.assertFalse((self.cache / "zed.guard").exists())
        self.assertIn("lock: held by this session", out)  # the old lock was this session's: it still holds it
        self.assertIn("Made before the rename", self.w.hw("task", "list").stdout)
        again = self.w.start().stdout
        self.assertNotIn("renamed", again)
        self.assertEqual(sorted(p.name for p in self.db.iterdir() if p.name.startswith(("hw", "zed"))),
                         sorted(p.name for p in self.db.iterdir() if p.name.startswith("hw")))

    def test_an_old_name_next_to_its_new_one_is_left_alone_and_start_says_which_database_holds_the_tasks(self):
        (self.db / "hw.db").write_bytes(b"")
        (self.db / "zed.db-journal").write_bytes(b"")
        out = self.w.start().stdout
        self.assertIn(f"OLD NAME LEFT ALONE: {self.db / 'zed.db'} and {self.db / 'hw.db'} both exist "
                      "(zed.db holds 1 task(s); hw.db holds 0 task(s) and is the one in use); keep the one that is current", out)
        self.assertTrue((self.db / "zed.db").exists())
        self.assertTrue((self.db / "zed.db-journal").exists())  # a side file stays with its database
        self.assertFalse((self.db / "hw.db-journal").exists())
        self.assertNotIn("Made before the rename", self.w.hw("task", "list").stdout)  # the new name is the one in use

    def test_a_database_that_cannot_be_read_is_reported_as_such_not_as_empty(self):
        (self.db / "hw.db").write_bytes(b"")
        (self.db / "zed.db-journal").write_bytes(b"left by a crashed write of the old code")  # needs a writer to recover
        self.assertIn("both exist (zed.db could not be read; hw.db holds 0 task(s) and is the one in use)", self.w.start().stdout)

    def test_a_side_file_of_the_database_moves_with_it(self):
        (self.db / "zed.db-journal").write_bytes(b"")
        self.w.start()
        self.assertEqual([p.name for p in self.db.iterdir() if p.name.startswith("zed")], [])
        self.assertIn("Made before the rename", self.w.hw("task", "list").stdout)

    def test_before_the_first_start_a_database_command_refuses_and_creates_no_empty_database(self):
        for command in (["status"], ["task", "list"], ["task", "add", "title=Lost"], ["q", "list"]):
            r = self.w.hw(*command, check=False)
            self.assertEqual(r.returncode, 1, command)
            self.assertEqual(r.stderr, f"hw: this profile is from before the rename to Herdwright: its database is still "
                                       f"{self.db / 'zed.db'}. Run `bin/hw start` first: it renames the profile's old names once.\n")
            self.assertFalse((self.db / "hw.db").exists(), command)
        self.w.start()
        self.assertIn("Made before the rename", self.w.hw("task", "list").stdout)

    def test_while_a_live_session_of_the_old_code_holds_the_old_lock_start_renames_nothing_and_stops(self):
        other = self.w.coordinator("upgraded")  # the old lock is held by the world's first coordinator, alive in its pane
        r = self.w.start(coordinator=other)
        self.assertIn(f"OLD COORDINATOR STILL RUNNING: {self.cache / 'zed.lock'} is held by a live session", r.stdout)
        self.assertIn("Nothing was renamed", r.stdout)
        self.assertEqual(len(r.stdout.splitlines()), 1, r.stdout)  # no digest: this session does not act
        for old in (self.db / "zed.db", self.cache / "zed.lock", self.cache / "zed.guard", *self.plugins):
            self.assertTrue(old.exists(), old)
        self.assertEqual(sorted(p.name for p in self.db.iterdir()), ["zed.db"])
        self.assertFalse((self.cache / "hw.lock").exists())

    def test_an_old_lock_whose_holder_is_gone_does_not_stop_the_renames(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        (self.cache / "zed.lock").write_text(json.dumps({"pane": None, "agent_session": None, "pid": gone.pid}))
        out = self.w.start().stdout
        self.assertIn(f"renamed {self.db / 'zed.db'} to hw.db", out)
        self.assertIn("lock: held by this session", out)

    def test_start_on_a_throwaway_profile_leaves_the_refs_of_the_checkout_under_test_alone(self):
        before = refs(HOME)
        self.assertIn("renamed", self.w.start().stdout)
        self.assertEqual(refs(HOME), before)


class OldNamesInACheckout(LoopCase):
    """What lives in the checkout and not in the profile: the sync point of hw-core-sync (a git ref) and the default
    place of the profile. A throwaway copy of this checkout stands in for it, on a profile branch, with the sync points
    of two branches under the old name. Nothing here changes the checkout under test."""

    def setUp(self):
        super().setUp()
        self.checkout = self.w.root / "checkout"
        tracked = subprocess.run(["git", "-C", str(HOME), "ls-files", "-z"], capture_output=True, text=True, check=True).stdout
        for name in filter(None, tracked.split("\0")):
            if (HOME / name).is_file():
                (self.checkout / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(HOME / name, self.checkout / name)
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "Start")
        self.git("checkout", "-q", "-b", "profile/ada")
        self.head = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/zed/core-sync/profile/ada", self.head)
        self.git("update-ref", "refs/zed/core-sync/profile/bob", self.head)  # another checkout's branch (a git worktree)

    def git(self, *args):
        return self.w.git(*args, cwd=self.checkout).stdout.strip()

    def start(self, **env):
        """`bin/hw start` of the copy, as the world's coordinator; `HW_PROFILE_DIR=None` leaves the profile to its default."""
        env = {k: v for k, v in (self.w.env() | env).items() if v is not None}
        r = subprocess.run([sys.executable, str(self.checkout / "bin" / "hw"), "start"], capture_output=True, text=True, cwd=self.w.root, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def own_profile(self):
        """The copy runs on its own `profile/` folder, as an install does by default."""
        shutil.copytree(self.w.profile, self.checkout / "profile")
        return {"HW_PROFILE_DIR": None}

    def test_with_the_profile_elsewhere_start_touches_no_ref_of_the_checkout(self):
        before = refs(self.checkout)
        self.assertNotIn("git ref", self.start())
        self.assertEqual(refs(self.checkout), before)

    def test_on_its_own_profile_start_renames_the_sync_point_of_its_branch_once_and_no_other(self):
        env = self.own_profile()
        self.assertIn("renamed git ref refs/zed/core-sync/profile/ada to refs/hw/core-sync/profile/ada", self.start(**env).splitlines())
        self.assertEqual([r for r in refs(self.checkout) if "core-sync" in r],
                         [f"refs/hw/core-sync/profile/ada {self.head}", f"refs/zed/core-sync/profile/bob {self.head}"])
        self.assertNotIn("git ref", self.start(**env))

    def test_an_old_sync_point_next_to_its_new_one_is_left_alone_and_reported(self):
        self.git("update-ref", "refs/hw/core-sync/profile/ada", self.head)
        before = refs(self.checkout)
        self.assertIn("OLD NAME LEFT ALONE: git refs refs/zed/core-sync/profile/ada and refs/hw/core-sync/profile/ada both exist; "
                      "refs/hw/core-sync/profile/ada is the one in use.", self.start(**self.own_profile()).splitlines())
        self.assertEqual(refs(self.checkout), before)

    def test_an_old_variable_is_not_read_and_start_names_it(self):
        elsewhere = self.w.root / "elsewhere"
        out = self.start(ZED_PROFILE_DIR=str(elsewhere))
        self.assertIn("NOT READ: ZED_PROFILE_DIR (the old names). Set HW_PROFILE_DIR instead.", out)
        self.assertIn(f"profile {self.w.profile}", out)
        self.assertFalse(elsewhere.exists())

    def test_with_only_the_old_variable_set_start_finds_no_profile_and_names_the_variable(self):
        out = self.start(HW_PROFILE_DIR=None, ZED_PROFILE_DIR=str(self.w.profile))
        self.assertEqual(out.splitlines(), ["NOT READ: ZED_PROFILE_DIR (the old names). Set HW_PROFILE_DIR instead.",
                                            f"NEW USER: there is no profile at {self.checkout / 'profile'}. Run the `onboarding` "
                                            "skill and do nothing else until it is done."])


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
