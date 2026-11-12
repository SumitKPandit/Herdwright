"""bin/hw-setup, run for real in a throwaway checkout with a PATH that holds exactly the tools a test wants found.

The script works on the checkout it lives in, so the throwaway checkout links to this one's bin/: nothing here touches
this checkout's own git config."""
import json
import os
import pty
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from _e2e import FAKES, clean_env

HOME = Path(__file__).resolve().parents[1]
REQUIRED = ("herdr", "git", "gh", "python3")
RUNTIMES = ("claude", "opencode")  # one of them is enough: an install runs on one
SHELL_TOOLS = ("dirname", "head", "env")  # what the script itself runs besides the tools it checks


class SetupCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.root / "hw"
        self.repo.mkdir()
        (self.repo / "bin").symlink_to(HOME / "bin")
        self.env = clean_env(self.root / "home")
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, capture_output=True, text=True, check=True).stdout.strip()

    def hooks_path(self):
        r = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=self.repo, env=self.env, capture_output=True, text=True)
        return r.stdout.strip()

    def setup(self, missing=(), cwd=None, runtimes=("claude",), stdin=subprocess.DEVNULL, args=()):
        """Run hw-setup with every required tool on PATH except `missing` (herdr, gh and claude are the fakes) and
        with `runtimes` installed (opencode is a script that only knows its version)."""
        tools = self.root / "tools"
        shutil.rmtree(tools, ignore_errors=True)
        tools.mkdir()
        for tool in (*REQUIRED, *SHELL_TOOLS, *(r for r in runtimes if r == "claude")):
            if tool not in missing:
                fake = FAKES / "bin" / tool
                (tools / tool).symlink_to(fake if fake.exists() else shutil.which(tool))
        if "opencode" in runtimes:
            (tools / "opencode").write_text("#!/bin/sh\necho 'opencode v9.9.9 (fake)'\n")
            (tools / "opencode").chmod(0o755)
        return subprocess.run([str(self.repo / "bin" / "hw-setup"), *args], cwd=cwd or self.root, env=self.env | {"PATH": str(tools)},
                              capture_output=True, text=True, stdin=stdin)

    def test_a_complete_machine_passes_and_gets_the_guard_hooks(self):
        r = self.setup()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for tool in ("herdr", "git", "gh", "python3"):
            self.assertRegex(r.stdout, rf"(?m)^ok +{tool}$")
        self.assertRegex(r.stdout, r"(?m)^ok +claude \(9\.9\.9 \(fake Claude Code\)\)$")
        self.assertNotIn("MISSING", r.stdout)
        self.assertIn("git guard hooks installed (core.hooksPath=hooks)", r.stdout)
        self.assertEqual(self.hooks_path(), "hooks")
        self.assertIn("no profile yet", r.stdout)

    def test_each_missing_tool_is_named_and_fails_the_run(self):
        for tool in REQUIRED:
            with self.subTest(tool=tool):
                r = self.setup(missing=[tool])
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertRegex(r.stdout, rf"(?m)^MISSING +{tool}\b")
                self.assertEqual(len([line for line in r.stdout.splitlines() if line.startswith("MISSING")]), 1)
                if tool != "git":  # the hooks are installed all the same (by git, when there is one)
                    self.assertEqual(self.hooks_path(), "hooks")

    def test_one_runtime_is_enough_and_none_fails_the_run(self):
        r = self.setup(runtimes=("opencode",))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"(?m)^ok +opencode \(opencode v9\.9\.9 \(fake\)\)$")
        self.assertNotIn("MISSING", r.stdout)
        self.assertRegex(r.stdout, r"(?m)^runtime +opencode \(the only one installed\)$")
        self.assertIn("'bin/hw coordinator' on OpenCode", r.stdout)
        r = self.setup(runtimes=())
        self.assertEqual(r.returncode, 1)
        self.assertRegex(r.stdout, r"(?m)^MISSING +a runtime: claude \(Claude Code\) or opencode \(OpenCode\)$")
        self.assertRegex(r.stdout, r"(?m)^MISSING +the runtime: Neither Claude Code nor OpenCode is installed, and an install uses one\. "
                                   r"Choose it: bin/hw-setup --runtime claude\|opencode$")

    def profile_settings(self, settings=None):
        (self.repo / "profile" / "config").mkdir(parents=True, exist_ok=True)
        (self.repo / "profile" / "profile.md").write_text("# A profile\n")
        path = self.repo / "profile" / "config" / "settings.json"
        if settings is not None:
            path.write_text(json.dumps(settings))
        return path

    def test_the_runtime_key_is_set_from_the_one_runtime_installed_and_other_settings_stay(self):
        path = self.profile_settings({"guard": {"protected": ["main"]}})
        r = self.setup(runtimes=("opencode",))
        self.assertRegex(r.stdout, rf'(?m)^runtime +opencode \(the only one installed; "runtime" in {path}\)$')
        self.assertEqual(json.loads(path.read_text()), {"guard": {"protected": ["main"]}, "runtime": "opencode"})
        r = self.setup(runtimes=("claude",))  # set once, it is the user's: a later run does not change it
        self.assertRegex(r.stdout, r"(?m)^runtime +opencode \(as stored; ")
        self.assertEqual(json.loads(path.read_text())["runtime"], "opencode")

    def test_with_both_runtimes_installed_it_asks_in_a_terminal_which_one_and_stores_the_answer(self):
        path = self.profile_settings({"assistant_name": "Ada"})
        r = self.in_a_terminal("opencode\n", runtimes=RUNTIMES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stderr, "Claude Code and OpenCode are both installed, and an install uses one. Which one (claude or opencode)? ")
        self.assertRegex(r.stdout, r"(?m)^runtime +opencode \(your answer; ")
        self.assertEqual(json.loads(path.read_text()), {"assistant_name": "Ada", "runtime": "opencode"})
        self.assertEqual(self.in_a_terminal("claude\n", runtimes=RUNTIMES).stderr, "")  # asked once

    def test_with_both_runtimes_and_no_answer_nothing_is_chosen_and_the_run_fails_until_one_is_named(self):
        path = self.profile_settings({"assistant_name": "Ada"})
        for r in (self.setup(runtimes=RUNTIMES), self.in_a_terminal("\n", runtimes=RUNTIMES)):  # no terminal; a bare Enter
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertRegex(r.stdout, r"(?m)^MISSING +the runtime: Claude Code and OpenCode are both installed, and an install uses one\. "
                                       r"Choose it: bin/hw-setup --runtime claude\|opencode$")
            self.assertNotIn("runtime", json.loads(path.read_text()))
        r = self.setup(runtimes=RUNTIMES, args=("--runtime", "claude"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(path.read_text())["runtime"], "claude")
        r = self.setup(runtimes=RUNTIMES, args=("--runtime", "pi"))
        self.assertEqual((r.returncode, r.stderr), (1, "hw: 'pi' is not a runtime: claude or opencode\n"))
        r = self.setup(args=("--model", "opus"))  # models are not setup's business
        self.assertEqual((r.returncode, r.stderr), (2, "usage: hw-setup [--runtime claude|opencode]\n"))
        for args in (("--runtime",), ("--runtime", ""), ("--runtime", "claude", "extra")):  # one value, nothing else
            r = self.setup(runtimes=RUNTIMES, args=args)
            self.assertEqual((r.returncode, r.stderr), (2, "usage: hw-setup [--runtime claude|opencode]\n"), args)

    def test_it_asks_nothing_about_models_and_writes_none(self):
        self.profile_settings({"runtime": "claude", "assistant_name": "Ada"})
        r = self.in_a_terminal("opus\n")
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(sorted(p.name for p in (self.repo / "profile" / "config").iterdir()), ["settings.json"])

    def in_a_terminal(self, typed, **kwargs):
        """Run hw-setup with a terminal as its input, in which the user types `typed`."""
        user, terminal = pty.openpty()
        self.addCleanup(os.close, user)
        self.addCleanup(os.close, terminal)
        os.write(user, typed.encode())
        return self.setup(stdin=terminal, **kwargs)

    def test_in_a_terminal_it_asks_once_what_to_call_the_assistant_and_stores_the_answer(self):
        path = self.profile_settings({"runtime": "claude"})
        r = self.in_a_terminal("Ada\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stderr, "What should your assistant be called? [Herdwright] ")
        self.assertRegex(r.stdout, r"(?m)^name +Ada \(change it: bin/hw name --set <name>\)$")
        self.assertEqual(json.loads(path.read_text()), {"runtime": "claude", "assistant_name": "Ada"})
        again = self.in_a_terminal("Grace\n")  # asked once: the stored name is the user's
        self.assertEqual(again.stderr, "")
        self.assertRegex(again.stdout, r"(?m)^name +Ada \(")
        self.assertEqual(json.loads(path.read_text())["assistant_name"], "Ada")

    def test_no_answer_or_no_terminal_keeps_the_default_name_and_stores_nothing(self):
        path = self.profile_settings({"runtime": "claude"})
        for r in (self.in_a_terminal("\n"), self.setup()):
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertRegex(r.stdout, r"(?m)^name +Herdwright \(change it: bin/hw name --set <name>\)$")
            self.assertEqual(json.loads(path.read_text()), {"runtime": "claude"})

    def test_without_a_profile_it_does_not_ask(self):
        r = self.in_a_terminal("Ada\n")
        self.assertEqual(r.stderr, "")
        self.assertNotRegex(r.stdout, r"(?m)^name ")

    def test_asked_for_help_it_says_what_it_does_and_changes_nothing(self):
        for flag in ("--help", "-h"):
            r = subprocess.run([str(self.repo / "bin" / "hw-setup"), flag], cwd=self.repo, env=self.env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertIn("hw-setup: check Herdwright's prerequisites", r.stdout)
            self.assertNotIn("ok ", r.stdout)
        self.assertEqual(self.hooks_path(), "")

    def test_running_it_again_changes_nothing(self):
        first, again = self.setup(), self.setup()
        self.assertEqual((again.returncode, again.stdout), (first.returncode, first.stdout))
        self.assertEqual(self.hooks_path(), "hooks")

    def test_it_works_on_its_own_checkout_wherever_it_is_run_from(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=elsewhere, env=self.env, check=True)
        self.assertEqual(self.setup(cwd=elsewhere).returncode, 0)
        self.assertEqual(self.hooks_path(), "hooks")
        r = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=elsewhere, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.stdout, "")

    def test_a_hooks_path_someone_else_set_is_left_alone(self):
        self.git("config", "core.hooksPath", ".husky")
        r = self.setup()
        self.assertIn("WARNING  core.hooksPath is '.husky'; not changing it", r.stderr)
        self.assertNotIn("git guard hooks installed", r.stdout)
        self.assertEqual(self.hooks_path(), ".husky")

    def test_hooks_that_will_stop_running_are_named(self):
        old = self.repo / ".git" / "hooks" / "pre-commit"
        old.parent.mkdir(exist_ok=True)
        old.write_text("#!/bin/sh\n")
        r = self.setup()
        self.assertIn("note     .git/hooks/pre-commit will no longer run (core.hooksPath=hooks)", r.stdout)
        self.assertNotIn(".sample will no longer run", r.stdout)

    def test_an_existing_profile_is_reported_with_its_branch(self):
        (self.repo / "profile").mkdir()
        (self.repo / "profile" / "profile.md").write_text("# A profile\n")
        self.git("checkout", "-q", "-b", "profile/me")
        r = self.setup()
        self.assertRegex(r.stdout, rf"(?m)^ok +profile present at {self.repo}/profile \(branch: profile/me\)$")
        self.assertNotIn("no profile yet", r.stdout)

    def test_a_profile_kept_elsewhere_is_found_through_its_variable(self):
        """S339: the script looked under the checkout only, said "no profile yet" and wrote no runtime."""
        elsewhere = self.root / "my-profile"
        (elsewhere / "config").mkdir(parents=True)
        (elsewhere / "profile.md").write_text("# A profile\n")
        self.env |= {"HW_PROFILE_DIR": str(elsewhere)}
        r = self.setup(runtimes=("opencode",))
        self.assertRegex(r.stdout, rf"(?m)^ok +profile present at {elsewhere} ")
        self.assertNotIn("no profile yet", r.stdout)
        self.assertEqual(json.loads((elsewhere / "config" / "settings.json").read_text()), {"runtime": "opencode"})


if __name__ == "__main__":
    unittest.main()
