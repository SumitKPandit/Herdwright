"""The install's runtime setting, the coordinator on OpenCode (`bin/zed coordinator`, and `start` in its session) and
`bin/zed relay`. OpenCode's server is the fake from test_zed_assign; tests/smoke_opencode.py runs the same flow against
the installed `opencode`."""
import json
import os
import stat
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from test_zed_assign import HOME, AssignCase, FakeOpenCodeServer


class FakeProcess:
    """Stands in for the server `open_coordinator` starts: this test process, so it is alive until it is stopped."""

    def __init__(self, command, env):
        self.command, self.env, self.pid, self.stopped = command, env, os.getpid(), False

    def terminate(self):
        self.stopped = True

    def wait(self, timeout=None):
        return 0


class CoordinatorCase(AssignCase):
    def setUp(self):
        super().setUp()
        self.server = FakeOpenCodeServer(self)
        self.processes, self.tui = [], []

        real_popen = subprocess.Popen

        def popen(command, *args, env=None, **kwargs):
            if command[:2] != ["opencode", "serve"]:  # git and the rest run for real
                return real_popen(command, *args, env=env, **kwargs)
            self.processes.append(FakeProcess(command, env))
            return self.processes[-1]

        def call(command, env=None, **kwargs):
            self.tui.append((command, env, (self.profile / ".cache" / "coordinator.json").exists()))
            return 3

        self.z.OPENCODE_SERVER_SECONDS = 0  # a server that is down is given up on at once

        for target, kwargs in ((self.z, {"attribute": "opencode_api", "side_effect": lambda *a, **k: self.server(*a, **k)}),
                               (self.z.subprocess, {"attribute": "Popen", "side_effect": popen}),
                               (self.z.subprocess, {"attribute": "call", "side_effect": call})):
            patcher = patch.object(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.settings({"runtime": "opencode"})
        self.record = self.profile / ".cache" / "coordinator.json"

    def settings(self, data):
        (self.profile / "config" / "settings.json").write_text(json.dumps(data))

    def installed(self, *programs):
        return patch.object(self.z.shutil, "which", side_effect=lambda name: f"/bin/{name}" if name in programs else None)

    def dies(self, call, *texts):
        self.output.seek(0)
        self.output.truncate(0)
        with self.assertRaises(SystemExit) as e:
            call()
        self.assertEqual(e.exception.code, 1)
        for text in texts:
            self.assertIn(text, self.output.getvalue())


class RuntimeTests(CoordinatorCase):
    def test_one_key_names_the_runtime_and_another_value_is_refused(self):
        self.assertEqual(self.z.install_runtime(), "opencode")
        self.settings({"runtime": "claude"})
        self.assertEqual(self.z.install_runtime(), "claude")
        self.settings({"runtime": "both"})
        self.dies(self.z.install_runtime, '"runtime": "both"', "is not one of claude, opencode")

    def test_without_the_key_the_runtime_is_the_one_installed_and_claude_when_both_are(self):
        self.settings({})
        for programs, runtime in ((("opencode",), "opencode"), (("claude",), "claude"), (("claude", "opencode"), "claude"), ((), "claude")):
            with self.installed(*programs):
                self.assertEqual(self.z.install_runtime(), runtime, programs)

    def test_a_worker_runs_on_the_installs_runtime_unless_spawn_names_one(self):
        sid = self.spawn("review")
        self.assertEqual(self.session(sid)["model"], "opencode/nemotron-3.5-super-vl-free")
        self.assertIn(("POST", "/session"), [c[:2] for c in self.server.calls])
        self.settings({"runtime": "claude"})
        self.server.calls.clear()
        sid = self.spawn("qa")
        self.assertEqual((self.session(sid)["model"], self.server.calls), ("claude-opus-5-5", []))

    def test_the_runtime_command_writes_the_key_only_when_one_runtime_is_installed(self):
        path = self.profile / "config" / "settings.json"
        self.settings({"worker": {"deny_tools": ["x"]}})
        with self.installed("claude", "opencode"):
            self.z.cmd_runtime(["--set-installed"])
        self.assertIn("both installed and an install uses one", self.output.getvalue())
        self.assertNotIn("runtime", json.loads(path.read_text()))
        with self.installed("opencode"):
            self.z.cmd_runtime(["--set-installed"])
        self.assertEqual(json.loads(path.read_text()), {"worker": {"deny_tools": ["x"]}, "runtime": "opencode"})
        self.settings({"runtime": "claude"})
        with self.installed("opencode"):  # set once, it is the user's
            self.z.cmd_runtime(["--set-installed"])
        self.assertEqual(json.loads(path.read_text()), {"runtime": "claude"})


class KitTests(CoordinatorCase):
    def test_the_config_dir_holds_model_variant_permissions_instructions_and_the_guard(self):
        (self.profile / "config" / "opencode.json").write_text(json.dumps({"model": "acme/m-1", "variant": "minimal"}))
        kit = self.z.coordinator_kit()
        self.assertEqual(kit["config_dir"], self.profile / ".cache" / "coordinator-opencode")
        self.assertEqual(kit["record"], self.record)
        config = json.loads((kit["config_dir"] / "opencode.json").read_text())
        self.assertEqual((config["model"], config["agents"]), ("acme/m-1", {"build": {"model": "acme/m-1#minimal"}}))
        self.assertEqual(config["instructions"], [str(self.profile / "profile.md"), str(self.profile / "lessons.md"),
                                                  str(HOME / "playbook.md"), str(HOME / "failure-modes.md")])
        rules = config["permissions"]
        self.assertTrue(all(r["action"] == "shell" and r["effect"] == "allow" for r in rules), rules)
        allowed = [r["resource"] for r in rules]
        for command in ("bin/zed *", f"{HOME}/bin/zed *", "git status *", "herdr agent *", "zed-pretool-canary *"):
            self.assertIn(command, allowed)
        claude = json.loads((HOME / ".claude" / "settings.json").read_text())["permissions"]["allow"]
        self.assertEqual(len([r for r in allowed if not r.startswith("/") and "canary" not in r]), len(claude))  # one list for both
        plugin = (kit["config_dir"] / "plugins" / "zed-pretool" / "index.js").read_text()
        self.assertIn(json.dumps(str(HOME / "bin" / "zed-pretool")), plugin)
        self.assertIn(json.dumps({"ZED_PROFILE_DIR": str(self.profile)}), plugin)
        self.assertEqual(kit["server"]["log"], kit["config_dir"] / "server.log")

    def test_without_a_profile_nothing_is_written_where_onboarding_will_create_it(self):
        (self.profile / "profile.md").unlink()
        before = sorted(p for p in self.profile.rglob("*"))
        kit = self.z.coordinator_kit()
        self.addCleanup(self.z.shutil.rmtree, kit["record"].parent, True)
        self.assertNotIn(self.profile, kit["config_dir"].parents)
        self.assertEqual(sorted(p for p in self.profile.rglob("*")), before)
        self.assertTrue((kit["config_dir"] / "plugins" / "zed-pretool" / "index.js").is_file())


class OpenTests(CoordinatorCase):
    def open(self):
        kit = self.z.coordinator_kit()
        return kit, *self.z.open_coordinator(kit)

    def test_a_proven_session_is_recorded_for_start_and_for_the_wake_call(self):
        kit, process, session, problem = self.open()
        self.assertEqual((session, problem), ("ses_test1", ""))
        port = kit["server"]["url"].rsplit(":", 1)[1]
        self.assertEqual(process.command, ["opencode", "serve", "--print-logs", "--log-level", "warn", "--hostname", "127.0.0.1", "--port", port])
        self.assertEqual(process.env["OPENCODE_CONFIG_DIR"], str(kit["config_dir"]))
        self.assertEqual(process.env["OPENCODE_PASSWORD"], kit["server"]["password"])
        self.assertEqual(process.env["ZED_COORDINATOR"], str(self.record))
        created = next(body for method, path, body in self.server.calls if (method, path) == ("POST", "/session"))
        self.assertEqual(created, {"location": {"directory": str(HOME)}, "title": "zed",
                                   "model": {"providerID": "opencode", "id": "nemotron-3.5-super-vl-free", "variant": "xhigh"}})
        self.assertRegex(self.server.prompts()[0], r"exactly this command: zed-pretool-canary [0-9a-f]{8}\n")
        record = json.loads(self.record.read_text())
        self.assertEqual({k: record[k] for k in ("runtime", "session", "server", "server_pid", "guard", "model", "variant")},
                         {"runtime": "opencode", "session": "ses_test1", "server_pid": os.getpid(), "guard": "live",
                          "server": {"url": kit["server"]["url"], "password": kit["server"]["password"]},
                          "model": "opencode/nemotron-3.5-super-vl-free", "variant": "xhigh"})
        self.assertEqual(stat.S_IMODE(self.record.stat().st_mode), 0o600)  # it holds the server's password

    def test_a_session_whose_guard_is_not_proven_is_not_recorded(self):
        for guard, up, model, why in (("dead", True, None, "was not denied (status completed): the coordinator guard (bin/zed-pretool) is not live"),
                                      ("silent", True, None, "the model made no canary call, so the coordinator guard (bin/zed-pretool) is not proven live"),
                                      ("live", False, None, "failed (ConnectionRefusedError"),
                                      ("live", True, {"providerID": "other", "id": "m"}, "the session runs {'providerID': 'other', 'id': 'm'}, not the configured")):
            with self.subTest(guard=guard, up=up, model=model):
                self.server = FakeOpenCodeServer(self, guard=guard, up=up, model=model)
                _, _, _, problem = self.open()
                self.assertIn(why, problem)
                self.assertFalse(self.record.exists())


class CommandTests(CoordinatorCase):
    def test_the_tui_opens_on_the_proven_session_and_the_server_and_record_end_with_it(self):
        with self.installed("opencode"), self.assertRaises(SystemExit) as e:
            self.z.cmd_coordinator([])
        self.assertEqual(e.exception.code, 3)  # the TUI's own exit code
        (command, env, recorded), = self.tui
        url = json.loads(json.dumps(command))[2]
        self.assertEqual(command, ["opencode", "--server", url, "--session", "ses_test1"])  # no --auto: the rules decide
        self.assertEqual(env["OPENCODE_CONFIG_DIR"], str(self.profile / ".cache" / "coordinator-opencode"))
        self.assertTrue(recorded)  # while the TUI ran
        self.assertTrue(self.processes[0].stopped)
        self.assertFalse(self.record.exists())

    def test_a_session_whose_guard_is_not_live_is_never_opened(self):
        self.server.guard = "dead"
        with self.installed("opencode"):
            self.dies(lambda: self.z.cmd_coordinator([]), "the coordinator was not opened:", "the coordinator guard (bin/zed-pretool) is not live")
        self.assertEqual(self.tui, [])
        self.assertTrue(self.processes[0].stopped)

    def test_it_is_for_an_opencode_install_and_never_for_a_worker(self):
        self.settings({"runtime": "claude"})
        self.dies(lambda: self.z.cmd_coordinator([]), "this install runs on Claude Code: start `claude` in")
        self.settings({"runtime": "opencode"})
        with patch.dict(os.environ, {"ZED_SESSION": "S7"}):
            self.dies(lambda: self.z.cmd_coordinator([]), "this is worker session S7, not Zed")
        with self.installed():
            self.dies(lambda: self.z.cmd_coordinator([]), "`opencode` is not installed")
        self.assertEqual((self.processes, self.tui), ([], []))


class StartTests(CoordinatorCase):
    def start(self, env=None, herdr_session=None, branch="profile/me"):
        """`bin/zed start` as the shell tool of an OpenCode session runs it: `env` is what that shell carries."""
        env = {"OPENCODE_SESSION_ID": "ses_test1", "ZED_COORDINATOR": str(self.record)} if env is None else env
        real_git = self.z.zedlib.git

        def git(path, *args, **kwargs):
            if args == ("branch", "--show-current"):
                return subprocess.CompletedProcess(args, 0, branch + "\n", "")
            return real_git(path, *args, **kwargs)

        self.output.seek(0)
        self.output.truncate(0)
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("OPENCODE_", "ZED_", "HERDR_", "CLAUDE"))}
        with patch.dict(os.environ, clean | env, clear=True), patch.object(self.z, "cmd_lock") as lock, \
                patch.object(self.z, "holds_lock", return_value=True), patch.object(self.z, "live_agents", return_value={}), \
                patch.object(self.z, "watcher_alive", return_value=None), patch.object(self.z.zedlib, "git", side_effect=git), \
                patch.object(self.z, "pane_session", return_value=herdr_session), \
                patch.object(self.z, "script_findings", return_value=[]):
            self.z.cmd_start([])
        return lock

    def proven(self, **changes):
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_text(json.dumps({"session": "ses_test1", "server_pid": os.getpid(), "guard": "live",
                                           "started_at": "2026-11-11 10:00"} | changes))

    def test_start_refuses_an_opencode_session_the_coordinator_command_did_not_open(self):
        refusal = "was not opened by `bin/zed coordinator`, so its guard (bin/zed-pretool) is not proven"
        self.dies(lambda: self.start(env={"OPENCODE_SESSION_ID": "ses_test1"}), refusal)  # plain `opencode`
        self.dies(self.start, refusal)  # the record is gone (its server stopped)
        self.proven(session="ses_other")
        self.dies(self.start, refusal)  # another session's proof
        self.proven(guard="unknown")
        self.dies(self.start, refusal)
        self.proven(server_pid=2 ** 22 + 12345)  # no such process
        self.dies(self.start, refusal)
        self.assertFalse((self.profile / ".cache" / "zed.lock").exists())

    def test_a_proven_session_starts_with_the_lock_and_a_digest_that_names_runtime_and_guard(self):
        self.proven()
        lock = self.start()
        lock.assert_called_once_with(["acquire"])
        out = self.output.getvalue()
        self.assertIn("runtime: opencode · session ses_test1 · guard proven live at 2026-11-11 10:00", out)
        self.assertIn(f"already in your instructions: {self.profile / 'profile.md'}, ", out)
        self.assertNotIn("read now:", out)
        self.assertNotIn("ON `main`", out)

    def test_the_guard_must_know_the_lock_holder_by_the_id_herdr_reports(self):
        self.proven()
        with patch.dict(os.environ, {"HERDR_PANE_ID": "w1:p1"}):
            self.dies(lambda: self.start(env={"OPENCODE_SESSION_ID": "ses_test1", "ZED_COORDINATOR": str(self.record),
                                              "HERDR_PANE_ID": "w1:p1"}, herdr_session="herdr-knows-another-id"),
                      "is ses_test1 to OpenCode but herdr-knows-another-id to herdr")

    def test_a_new_user_is_sent_to_onboarding_and_nothing_else_happens(self):
        (self.profile / "profile.md").unlink()
        self.proven()
        lock = self.start()
        lock.assert_not_called()
        self.assertEqual(self.output.getvalue().strip(),
                         f"NEW USER: there is no profile at {self.profile}. Run the `onboarding` skill and do nothing else until "
                         "it is done. Then tell the user to quit and run `bin/zed coordinator` again.")

    def test_claude_code_needs_no_coordinator_command_and_is_told_what_to_read_and_when_it_is_on_main(self):
        self.settings({"runtime": "claude"})
        (self.profile / "lessons.md").write_text("# Lessons\n")
        self.start(env={"CLAUDE_CODE_SESSION_ID": "c1"}, branch="main")
        out = self.output.getvalue()
        self.assertIn("\nruntime: claude\n", out)
        self.assertIn(f"read now: {self.profile / 'profile.md'}, {self.profile / 'lessons.md'}, {HOME / 'playbook.md'}, {HOME / 'failure-modes.md'}", out)
        self.assertIn("ON `main`: it holds core only.", out)
        (self.profile / "profile.md").unlink()
        self.start(env={"CLAUDE_CODE_SESSION_ID": "c1"})
        self.assertEqual(self.output.getvalue().strip(), f"NEW USER: there is no profile at {self.profile}. Run the `onboarding` "
                                                         "skill and do nothing else until it is done.")


class RelayTests(CoordinatorCase):
    def setUp(self):
        super().setUp()
        self.settings({"runtime": "claude"})
        self.brief.write_text("# Brief\n\n## Goal\nx\n\n## Decisions already made by the user\n- User (earlier): \"ship it\"\n\n## Plan\n- one\n\n## Definition of done\n- done\n")
        self.sid = self.spawn("build")
        self.q1 = self.z.add_question(self.conn, 20, self.sid, "Which port?\nOptions: 9000 or 9196.")
        self.q2 = self.z.add_question(self.conn, 20, self.sid, "Rename the flag?")
        self.conn.commit()
        self.mock_prompt_worker.reset_mock()
        self.state = patch.object(self.z, "session_agent_state", return_value="idle")
        self.state.start()
        self.addCleanup(self.state.stop)

    def question(self, qid):
        return self.conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()

    def test_one_command_prompts_the_worker_records_the_answers_and_notes_them_in_the_brief(self):
        self.assertEqual(self.session(self.sid)["status"], "waiting_user")
        self.z.cmd_relay([f"Q{self.q1}", "use port 9196", f"Q{self.q2}", "No, keep --dry-run."])
        (agent, text), = [c.args for c in self.mock_prompt_worker.call_args_list]  # one prompt for the worker's two answers
        self.assertEqual(agent, "build-t20")
        self.assertEqual(text, f"Answer from the user to your question Q{self.q1} (Which port?): use port 9196\n"
                               f"Answer from the user to your question Q{self.q2} (Rename the flag?): No, keep --dry-run.\nContinue.")
        self.assertEqual([(q["status"], q["answer"]) for q in map(self.question, (self.q1, self.q2))],
                         [("answered", "use port 9196"), ("answered", "No, keep --dry-run.")])
        self.assertEqual(self.session(self.sid)["status"], "running")
        self.assertEqual(self.conn.execute("SELECT status FROM tasks WHERE id=20").fetchone()[0], "active")
        brief = self.brief.read_text()
        decisions = brief[brief.index("## Decisions already made by the user"):brief.index("## Plan")]  # the section's end
        self.assertRegex(decisions, r'- User \(earlier\): "ship it"\n- User \(\d{4}-\d\d-\d\d \d\d:\d\d\), Q%d "Which port\?": "use port 9196"\n'
                                    r'- User \([^)]+\), Q%d "Rename the flag\?": "No, keep --dry-run\."\n\n$' % (self.q1, self.q2))
        self.assertIn(f"recorded Q{self.q1}, Q{self.q2}", self.output.getvalue())

    def test_a_working_session_is_not_interrupted_and_nothing_is_recorded(self):
        self.state.stop()
        with patch.object(self.z, "session_agent_state", return_value="working"):
            self.dies(lambda: self.z.cmd_relay([f"Q{self.q1}", "use port 9196"]), f"refused: S{self.sid}")
            self.assertEqual(self.question(self.q1)["status"], "open")
            self.assertNotIn("9196", self.brief.read_text())
            self.mock_prompt_worker.assert_not_called()
            self.z.cmd_relay([f"Q{self.q1}", "use port 9196", "--redirect"])
        self.state.start()
        self.assertEqual(self.question(self.q1)["status"], "answered")
        self.mock_prompt_worker.assert_called_once()

    def test_a_prompt_that_fails_leaves_the_question_open(self):
        self.mock_prompt_worker.return_value = (False, "agent_prompt_stalled")
        self.dies(lambda: self.z.cmd_relay([f"Q{self.q1}", "use port 9196"]), "failed: agent_prompt_stalled")
        self.assertEqual(self.question(self.q1)["status"], "open")
        self.assertNotIn("9196", self.brief.read_text())

    def test_a_question_no_session_asked_is_only_recorded_and_bad_input_changes_nothing(self):
        own = self.z.add_question(self.conn, None, None, "Zed's own question")
        self.conn.commit()
        self.z.cmd_relay([f"Q{own}", "yes"])
        self.assertEqual(self.question(own)["answer"], "yes")
        self.mock_prompt_worker.assert_not_called()
        self.dies(lambda: self.z.cmd_relay([f"Q{self.q1}"]), "one quoted answer after each question id")
        self.dies(lambda: self.z.cmd_relay([f"Q{self.q1}", "a", "Q999", "b"]), "no question Q999")
        self.dies(lambda: self.z.cmd_relay([f"Q{self.q1}", "  "]), f"no answer given for Q{self.q1}")
        self.assertEqual(self.question(self.q1)["status"], "open")
        self.mock_prompt_worker.assert_not_called()

    def test_a_brief_without_the_section_gets_one(self):
        self.brief.write_text("# Brief\n\n## Plan\n- one\n")
        self.z.cmd_relay([f"Q{self.q2}", "yes"])
        self.assertRegex(self.brief.read_text(), r'## Plan\n- one\n\n## Decisions already made by the user\n- User \([^)]+\), Q%d "Rename the flag\?": "yes"\n$' % self.q2)


if __name__ == "__main__":
    unittest.main()
