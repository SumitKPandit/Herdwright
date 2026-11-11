"""A Claude Code worker and the target repo's own instructions: the startup dialogs spawn answers, the precedence the
worker rules state, the coordinator manual that a worker in this repo cannot follow, and the launch that lets Claude
Code pick up the repo's `CLAUDE.md`, `.claude/rules`, `.claude/skills` and `.claude/settings.json` by itself.
herdr is stubbed (test_zed_assign.AssignCase); the dialog texts are the ones Claude Code v2.1.322 shows."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_zed_assign import HOME, AssignCase

TRUST_SCREEN = " Accessing workspace:\n ❯ No, exit\n   Yes, I trust this folder\n Enter to confirm · Esc to cancel"
IMPORT_SCREEN = ("  Allow external CLAUDE.md file imports?\n  External imports:\n    /elsewhere/AGENTS.md\n"
                 "  ❯ No, disable external imports\n    Yes, allow external imports\n Enter to confirm · Esc to cancel")
READY_SCREEN = "❯ \n  ⏵⏵ auto mode on"


class StartupDialogTests(AssignCase):
    def spawn_through(self, *screens):
        """Spawn against a fake CLI that shows `screens` in order before its prompt; each key press closes the one on
        screen. Returns the keys spawn sent, one list per press."""
        screens, keys = [*screens, READY_SCREEN], []
        blocked, stubbed_run = len(screens) > 1, self.mock_run.side_effect

        def run(cmd, check=True, cwd=None):
            done = stubbed_run(cmd, check=check, cwd=cwd)  # records herdr calls; git stays real
            if cmd[:3] == ["herdr", "agent", "start"] and blocked:
                return subprocess.CompletedProcess(cmd, 1, "", '{"error":{"code":"agent_not_ready"}}')
            if cmd[:3] == ["herdr", "agent", "read"]:
                return subprocess.CompletedProcess(cmd, 0, screens[0], "")
            if cmd[:3] == ["herdr", "agent", "send-keys"]:
                keys.append(cmd[4:])
                screens.pop(0)
            return done

        def herdr_json(args):
            if args[:2] == ["agent", "get"]:
                return {"result": {"agent": {"agent_status": "idle" if screens[0] == READY_SCREEN else "blocked"}}}
            return {"result": {"workspace": {"workspace_id": "w1"}, "root_pane": {"pane_id": "p1"}}}

        self.mock_run.side_effect = run
        self.mock_herdr_json.side_effect = herdr_json
        self.sid = self.spawn("build")
        return keys

    def test_no_dialog_sends_no_keys(self):
        self.assertEqual(self.spawn_through(), [])
        self.assertNotIn("startup dialog", self.output.getvalue())
        self.assertNotIn(["herdr", "agent", "read"], [c[:3] for c in self.herdr_calls])

    def test_folder_trust_is_accepted(self):
        self.assertEqual(self.spawn_through(TRUST_SCREEN), [["down", "enter"]])
        self.assertIn("startup dialog: folder trust: accepted", self.output.getvalue())

    def test_external_imports_are_declined_and_reported(self):
        self.assertEqual(self.spawn_through(IMPORT_SCREEN), [["enter"]])  # "No, disable external imports" is preselected
        self.assertIn("startup dialog: external CLAUDE.md imports: declined: instructions imported from outside the "
                      "worktree are not loaded", self.output.getvalue())
        self.assertEqual(self.session(self.sid)["status"], "running")
        self.mock_prompt_worker.assert_called_once()  # the brief was handed over

    def test_both_dialogs_in_a_row_are_answered(self):
        self.assertEqual(self.spawn_through(TRUST_SCREEN, IMPORT_SCREEN), [["down", "enter"], ["enter"]])
        out = self.output.getvalue()
        self.assertLess(out.index("startup dialog: folder trust"), out.index("startup dialog: external CLAUDE.md imports"))

    def test_a_dialog_that_stays_on_screen_is_answered_once(self):
        keys = []

        def run(cmd, check=True, cwd=None):
            if cmd[:3] == ["herdr", "agent", "send-keys"]:
                keys.append(cmd[4:])
            return subprocess.CompletedProcess(cmd, 0, IMPORT_SCREEN, "")

        self.mock_run.side_effect = run
        self.mock_herdr_json.side_effect = lambda args: {"result": {"agent": {"agent_status": "blocked"}}}
        self.assertEqual(self.z.answer_start_dialogs("build-t20", seconds=2), ([self.z.START_DIALOGS[1]], False))
        self.assertEqual(keys, [["enter"]])

    def test_an_unknown_dialog_is_left_blocked_for_zed(self):
        with self.assertRaises(SystemExit):
            self.spawn_through(" Some new dialog\n ❯ Yes\n   No")
        self.assertIn("agent start did not report ready", self.output.getvalue())
        self.assertNotIn(["herdr", "agent", "send-keys"], [c[:3] for c in self.herdr_calls])
        self.mock_prompt_worker.assert_not_called()


class PrecedenceTests(AssignCase):
    def test_worker_rules_state_which_rules_win(self):
        kit, _ = self.z.hat_kit("build", 7, "build-t20", self.worktree)
        rules = " ".join(kit["rules"].read_text().split())
        section = rules[rules.index("## The repository's own instructions"):rules.index("## The no-assumption rule")]
        self.assertIn("They apply to you: read them and use them", section)
        for name in ("`CLAUDE.md`", "`.claude/rules`", "`.claude/skills`"):
            self.assertIn(name, section)
        self.assertIn("these rules win, and you report the conflict to Zed", section)
        enforced = section[section.index("these rules win"):section.index("Nothing else is overridden")]
        self.assertEqual(enforced.count(" - "), 4)
        for point in ("the commit format guard", "no outward posting without the user's approval", "QA before a PR",
                      "your hat's deny rules"):
            self.assertIn(f" - {point}", enforced)
        self.assertIn("Nothing else is overridden: on every other point the repository's instructions stand.", section)


class ToolInsideItselfTests(AssignCase):
    def test_start_refuses_a_worker_before_touching_the_lock(self):
        with patch.dict(os.environ, {"ZED_SESSION": "S7"}), patch.object(self.z, "cmd_lock") as lock, \
                self.assertRaises(SystemExit) as e:
            self.z.cmd_start(["--take-over"])
        self.assertEqual(e.exception.code, 1)
        lock.assert_not_called()
        self.assertIn("this is worker session S7, not Zed", self.output.getvalue())

    def test_the_real_command_refuses_with_the_environment_spawn_gives_a_worker(self):
        _, env = self.z.hat_kit("build", 7, "build-t20", self.worktree)
        self.assertIn("ZED_SESSION=S7", env)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "profile.md").write_text("# throwaway\n")
            worker = {**os.environ, **dict(e.split("=", 1) for e in env), "ZED_PROFILE_DIR": tmp}
            for args in (["start"], ["start", "--take-over"]):
                r = subprocess.run([sys.executable, str(HOME / "bin" / "zed"), *args], env=worker, capture_output=True,
                                   text=True, timeout=30)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("not Zed", r.stderr)
            self.assertEqual([p.name for p in Path(tmp).rglob("*") if p.is_file()], ["profile.md"])  # no lock, no DB

    def test_the_manual_says_who_it_is_for_before_it_says_you_are_zed(self):
        manual = (HOME / "CLAUDE.md").read_text()
        self.assertLess(manual.index("If `ZED_SESSION` is set"), manual.index("You are **Zed**"))
        self.assertLess(manual.index("nothing below applies to you"), manual.index("You are **Zed**"))


class LaunchTests(AssignCase):
    # Flags that would stop Claude Code loading the repo's own files or change where it starts.
    ISOLATING = {"--bare", "--safe-mode", "--setting-sources", "--system-prompt", "--system-prompt-file", "--cwd"}

    def test_the_worker_starts_in_the_worktree_with_the_repo_files_left_to_claude_code(self):
        self.spawn("build")
        create = next(c for c in self.herdr_calls if c[:3] == ["herdr", "workspace", "create"])
        self.assertEqual(create[create.index("--cwd") + 1], str(self.worktree))
        start = next(c for c in self.herdr_calls if c[:3] == ["herdr", "agent", "start"])
        self.assertEqual(start[start.index("--kind") + 1], "claude")
        self.assertEqual(start[start.index("--pane") + 1], "p1")  # the pane that was opened in the worktree
        cli = start[start.index("--") + 1:]
        self.assertEqual(self.ISOLATING & set(cli), set())
        self.assertEqual([cli[i + 1] for i, a in enumerate(cli) if a == "--add-dir"], [str(self.profile)])
        self.assertIn("--append-system-prompt-file", cli)  # the worker rules are added to the prompt, not put in its place

    def test_the_per_session_settings_only_add_the_deny_list_and_hooks(self):
        kit, _ = self.z.hat_kit("build", 7, "build-t20", self.worktree)
        cli = self.z.claude_launch("build-t20", None, None, kit)
        settings = json.loads(Path(cli[cli.index("--settings") + 1]).read_text())
        self.assertEqual(sorted(settings), ["hooks", "permissions"])
        self.assertEqual(sorted(settings["permissions"]), ["deny"])  # no allow list, no mode: the repo's own stay
        self.assertEqual(sorted(settings["hooks"]), ["PreToolUse", "Stop"])
        self.assertFalse(str(kit["claude_settings"]).startswith(str(self.worktree)))  # the repo's .claude/ is untouched
        self.assertEqual(list(self.worktree.glob(".claude/*")), [])


if __name__ == "__main__":
    unittest.main()
