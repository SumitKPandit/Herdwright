"""A Claude Code worker and the target repo's own instructions: the startup dialogs spawn answers, the precedence the
worker rules state, the coordinator manual that a worker in this repo cannot follow, and the launch that lets Claude
Code pick up the repo's `CLAUDE.md`, `.claude/rules`, `.claude/skills` and `.claude/settings.json` by itself.
herdr is stubbed (test_hw_assign.AssignCase); the dialog texts are the ones Claude Code v2.1.322 shows."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_hw_assign import HOME, AssignCase

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
        self.assertIn(["herdr", "agent", "read"], [c[:3] for c in self.herdr_calls])  # idle alone is not ready: the screen is read
        self.mock_prompt_worker.assert_called_once()

    def idle_with(self, *screens):
        """herdr reports the agent started and idle the whole time, whatever `screens` show in order before the prompt
        (the gap the T99 review named: idle between two dialogs). Returns the keys sent, one list per press."""
        screens, keys = [*screens, READY_SCREEN], []
        stubbed_run = self.mock_run.side_effect

        def run(cmd, check=True, cwd=None):
            if cmd[:3] == ["herdr", "agent", "read"]:
                return subprocess.CompletedProcess(cmd, 0, screens[0], "")
            if cmd[:3] == ["herdr", "agent", "send-keys"]:
                keys.append(cmd[4:])
                screens.pop(0)
            return stubbed_run(cmd, check=check, cwd=cwd)

        self.mock_run.side_effect = run
        self.mock_prompt_worker.side_effect = lambda agent, text: self.assertEqual(screens, [READY_SCREEN]) or (True, "")
        return keys

    def test_idle_with_a_dialog_on_screen_is_not_ready_the_dialog_is_answered_before_the_brief(self):
        keys = self.idle_with(IMPORT_SCREEN)
        self.spawn("build")
        self.assertEqual(keys, [["enter"]])
        self.assertIn("startup dialog: external CLAUDE.md imports: declined", self.output.getvalue())
        self.mock_prompt_worker.assert_called_once()  # typed only once the prompt, not the dialog, was on screen

    def test_idle_between_two_dialogs_answers_both_before_the_brief(self):
        keys = self.idle_with(TRUST_SCREEN, IMPORT_SCREEN)
        self.spawn("build")
        self.assertEqual(keys, [["down", "enter"], ["enter"]])
        self.mock_prompt_worker.assert_called_once()

    def test_idle_with_a_dialog_that_never_closes_gets_no_brief(self):
        keys = self.idle_with(IMPORT_SCREEN, IMPORT_SCREEN, IMPORT_SCREEN)  # the same dialog is still there after the answer
        with patch.object(self.z.time, "monotonic", side_effect=iter(range(0, 10000, 10)).__next__), self.assertRaises(SystemExit):
            self.spawn("build")
        self.assertEqual(keys, [["enter"]])  # answered once, never pressed again
        self.assertIn("agent start did not report ready", self.output.getvalue())
        self.mock_prompt_worker.assert_not_called()

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
        with patch.object(self.z.time, "monotonic", side_effect=iter(range(0, 10000, 10)).__next__):  # 10 s a look
            self.assertEqual(self.z.answer_start_dialogs("build-t20", started=False, seconds=60), ([self.z.START_DIALOGS[1]], False))
        self.assertEqual(keys, [["enter"]])

    def test_an_unknown_dialog_is_left_blocked_for_hw(self):
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
        self.assertIn("these rules win, and you report the conflict to Herdwright", section)
        enforced = section[section.index("these rules win"):section.index("Nothing else is overridden")]
        self.assertEqual(enforced.count(" - "), 4)
        for point in ("the commit format guard", "no outward posting without the user's approval", "QA before a PR",
                      "your hat's deny rules"):
            self.assertIn(f" - {point}", enforced)
        self.assertIn("Nothing else is overridden: on every other point the repository's instructions stand.", section)


class ToolInsideItselfTests(AssignCase):
    def test_start_refuses_a_worker_before_touching_the_lock(self):
        with patch.dict(os.environ, {"HW_SESSION": "S7"}), patch.object(self.z, "cmd_lock") as lock, \
                self.assertRaises(SystemExit) as e:
            self.z.cmd_start(["--take-over"])
        self.assertEqual(e.exception.code, 1)
        lock.assert_not_called()
        self.assertIn("this is worker session S7, not Herdwright", self.output.getvalue())

    def test_the_real_command_refuses_with_the_environment_spawn_gives_a_worker(self):
        _, env = self.z.hat_kit("build", 7, "build-t20", self.worktree)
        self.assertIn("HW_SESSION=S7", env)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "profile.md").write_text("# throwaway\n")
            worker = {**os.environ, **dict(e.split("=", 1) for e in env), "HW_PROFILE_DIR": tmp}
            for args in (["start"], ["start", "--take-over"]):
                r = subprocess.run([sys.executable, str(HOME / "bin" / "hw"), *args], env=worker, capture_output=True,
                                   text=True, timeout=30)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("not Herdwright", r.stderr)
            self.assertEqual([p.name for p in Path(tmp).rglob("*") if p.is_file()], ["profile.md"])  # no lock, no DB

    def test_the_manual_says_who_it_is_for_before_it_says_who_you_are_and_takes_your_name_from_the_profile(self):
        manual = (HOME / "AGENTS.md").read_text()
        you_are = manual.index("You are a personal assistant")
        self.assertLess(manual.index("If `HW_SESSION` is set"), you_are)
        self.assertLess(manual.index("nothing below applies to you"), you_are)
        self.assertIn('Your name is the one in the\nprofile: `bin/hw start` prints it ("Herdwright" when none is set)', manual)

    def test_one_manual_serves_both_runtimes(self):
        # OpenCode reads AGENTS.md and never CLAUDE.md; Claude Code reads CLAUDE.md, which only imports the manual from
        # inside the repo (an import from inside the repo raises no dialog; tests/smoke_opencode.py and a Claude Code
        # run prove the loading itself)
        self.assertEqual((HOME / "CLAUDE.md").read_text(), "@AGENTS.md\n")
        manual = (HOME / "AGENTS.md").read_text()
        differences = manual[manual.index("## Runtime differences"):manual.index("## Talking to the user")]
        for part in ("**Claude Code.**", "**OpenCode.**", "`bin/hw coordinator`", "Stop\n  hooks", "`.claude/settings.json`"):
            self.assertIn(part, differences)
        rest = manual.replace(differences, "")
        for runtime_only in ("Stop hook", "run_in_background", "PreToolUse", "bin/hw coordinator", "claude.json", "opencode.json"):
            self.assertNotIn(runtime_only, rest, "runtime-specific text belongs under Runtime differences")
        self.assertIn("Skills in `.claude/skills/` (both runtimes load them from there)", manual)

    def test_the_manual_and_onboarding_leave_model_effort_and_sessions_to_the_user_and_name_the_command(self):
        manual = " ".join((HOME / "AGENTS.md").read_text().split())
        for part in ("Herdwright ships no model or effort and I never pick one.",
                     "I run `bin/hw configure --model <m> --effort <e> --max-sessions <n>` with what they said",
                     "`--clear <key>` drops a choice) and tell them what it printed. It holds for every later spawn.",
                     "what is not set there is not passed, so the user's own Claude Code default applies",
                     "so a model is needed: `coordinator` asks for it once, in the terminal"):
            self.assertIn(part, manual)
        onboarding = " ".join((HOME / ".claude" / "skills" / "onboarding" / "SKILL.md").read_text().split())
        for part in ("Model and effort: do not ask and do not choose.", "`bin/hw-setup --runtime claude|opencode`",
                     "`cp -Rn profile.template/. profile/`", "only for what the user asked for"):
            self.assertIn(part, onboarding)
        self.assertNotIn("cp -R profile.template profile", onboarding)  # it would overwrite what setup stored

    def test_the_manual_gives_one_command_per_ordered_routine(self):
        manual = (HOME / "AGENTS.md").read_text()
        start = manual[manual.index("**At the start of every session**"):manual.index("## One active Herdwright per profile")]
        self.assertIn("run `bin/hw start` and do what it prints, nothing before it", start)
        self.assertNotRegex(start, r"(?m)^\d+\. ")  # no numbered steps for the model to keep in order
        self.assertIn('`bin/hw relay Q<id> "<the user\'s answer, verbatim>"`', manual)


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
