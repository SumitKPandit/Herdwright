"""bin/zed-setup, run for real in a throwaway checkout with a PATH that holds exactly the tools a test wants found.

The script works on the checkout it lives in, so the throwaway checkout links to this one's bin/: nothing here touches
this checkout's own git config."""
import json
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
        self.repo = self.root / "zed"
        self.repo.mkdir()
        (self.repo / "bin").symlink_to(HOME / "bin")
        self.env = clean_env(self.root / "home")
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env, capture_output=True, text=True, check=True).stdout.strip()

    def hooks_path(self):
        r = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=self.repo, env=self.env, capture_output=True, text=True)
        return r.stdout.strip()

    def setup(self, missing=(), cwd=None, runtimes=("claude",)):
        """Run zed-setup with every required tool on PATH except `missing` (herdr, gh and claude are the fakes) and
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
        return subprocess.run([str(self.repo / "bin" / "zed-setup")], cwd=cwd or self.root, env=self.env | {"PATH": str(tools)},
                              capture_output=True, text=True)

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
        self.assertIn("'bin/zed coordinator' on OpenCode (now: opencode)", r.stdout)
        r = self.setup(runtimes=())
        self.assertEqual(r.returncode, 1)
        self.assertRegex(r.stdout, r"(?m)^MISSING +a runtime: claude \(Claude Code\) or opencode \(OpenCode\)$")

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
        self.assertRegex(r.stdout, r"(?m)^runtime +runtime set to opencode in .*settings\.json \(the only one installed\)$")
        self.assertEqual(json.loads(path.read_text()), {"guard": {"protected": ["main"]}, "runtime": "opencode"})
        r = self.setup(runtimes=("claude",))  # set once, it is the user's: a later run does not change it
        self.assertRegex(r.stdout, r"(?m)^runtime +opencode$")
        self.assertEqual(json.loads(path.read_text())["runtime"], "opencode")

    def test_with_both_runtimes_installed_the_choice_is_left_to_the_user(self):
        path = self.profile_settings()
        r = self.setup(runtimes=RUNTIMES)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('Claude Code and OpenCode are both installed and an install uses one: set "runtime"', r.stdout)
        self.assertIn("(now: claude)", r.stdout)
        self.assertFalse(path.exists())

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
        self.assertRegex(r.stdout, r"(?m)^ok +profile/ present \(branch: profile/me\)$")
        self.assertNotIn("no profile yet", r.stdout)


if __name__ == "__main__":
    unittest.main()
