"""The install's runtime setting, the coordinator on OpenCode (`bin/hw coordinator`, and `start` in its session) and
`bin/hw relay`. OpenCode's server is the fake from test_hw_assign; tests/smoke_opencode.py runs the same flow against
the installed `opencode`."""
import json
import os
import stat
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from test_hw_assign import HOME, AssignCase, FakeOpenCodeServer


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
        self.assertEqual(self.session(sid)["model"], "acme/base")
        self.assertIn(("POST", "/session"), [c[:2] for c in self.server.calls])
        self.settings({"runtime": "claude"})
        self.server.calls.clear()
        sid = self.spawn("qa")
        self.assertEqual((self.session(sid)["model"], self.server.calls), ("claude-opus-5-5", []))

    def test_choosing_the_runtime_stores_the_one_installed_and_leaves_both_or_neither_to_the_user(self):
        path = self.profile / "config" / "settings.json"
        for programs, found in ((("claude", "opencode"), "Claude Code and OpenCode are both installed"), ((), "Neither Claude Code nor OpenCode is installed")):
            self.settings({"worker": {"deny_tools": ["x"]}})
            with self.installed(*programs):  # no terminal here: nobody to ask
                self.dies(lambda: self.z.cmd_runtime(["--choose"]))
            self.assertIn(f"MISSING  the runtime: {found}, and an install uses one. Choose it: bin/hw-setup --runtime claude|opencode", self.output.getvalue())
            self.assertNotIn("runtime", json.loads(path.read_text()))
            with self.installed(*programs), patch.object(self.z, "ask_user", return_value="opencode") as ask:
                self.z.cmd_runtime(["--choose"])
            self.assertEqual(ask.call_args.args, (f"{found}, and an install uses one. Which one (claude or opencode)?",))
            self.assertEqual(json.loads(path.read_text()), {"worker": {"deny_tools": ["x"]}, "runtime": "opencode"})
        self.settings({})
        with self.installed("opencode"):
            self.z.cmd_runtime(["--choose"])
        self.assertEqual(json.loads(path.read_text()), {"runtime": "opencode"})
        with self.installed("claude"):  # set once, it is the user's
            self.z.cmd_runtime(["--choose"])
        self.assertEqual(json.loads(path.read_text()), {"runtime": "opencode"})
        with self.installed("opencode"):  # until they name another
            self.z.cmd_runtime(["--choose", "claude"])
            self.dies(lambda: self.z.cmd_runtime(["--choose", "pi"]), "'pi' is not a runtime: claude or opencode")
        self.assertEqual(json.loads(path.read_text()), {"runtime": "claude"})

    def test_before_there_is_a_profile_only_a_choice_the_user_made_is_stored_and_the_template_settings_come_with_it(self):
        (self.profile / "profile.md").unlink()
        path = self.profile / "config" / "settings.json"
        path.unlink()
        with self.installed("opencode"):
            self.z.cmd_runtime(["--choose"])
        self.assertIn("runtime  opencode (the only one installed)", self.output.getvalue())
        self.assertFalse(path.exists())
        with self.installed("claude", "opencode"):
            self.z.cmd_runtime(["--choose", "opencode"])
        template = json.loads((HOME / "profile.template" / "config" / "settings.json").read_text())
        self.assertEqual(json.loads(path.read_text()), template | {"runtime": "opencode"})  # onboarding keeps the files already there


class ModelQuestionTests(CoordinatorCase):
    """On OpenCode `coordinator` asks once for the model: a session its server creates without one runs the server's
    own pick, not the user's."""

    def setUp(self):
        super().setUp()
        self.config = self.profile / "config" / "opencode.json"
        self.config.unlink()
        self.state = self.root / "state"
        (self.state / "opencode").mkdir(parents=True)
        self.enterContext(patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)}))

    def last_used(self, text):
        (self.state / "opencode" / "model.json").write_text(text)

    def answers(self, *typed):
        return patch.object(self.z, "ask_user", side_effect=list(typed))

    def test_enter_accepts_the_model_and_variant_the_tui_last_used_and_they_are_stored(self):
        self.last_used(json.dumps({"recent": [{"providerID": "acme", "modelID": "last/used"}], "variant": {"acme/last/used": "xhigh"}}))
        with self.answers("", "") as ask:
            self.z.choose_opencode_model()
        self.assertEqual([c.args[0] for c in ask.call_args_list],
                         ["Which model should Herdwright and its workers run on (provider/model, as `opencode models` lists them)? [acme/last/used]",
                          "Which variant of it (the model's own name for how hard it thinks; nothing: its default)? [xhigh]"])
        self.assertEqual(json.loads(self.config.read_text()), {"model": "acme/last/used", "variant": "xhigh"})
        self.assertIn(f"stored: model acme/last/used, variant xhigh in {self.config}", self.output.getvalue())
        with self.answers() as ask:  # asked once
            self.z.choose_opencode_model()
        ask.assert_not_called()

    def test_a_typed_model_wins_and_its_variant_is_not_guessed_from_another_model(self):
        self.last_used(json.dumps({"recent": [{"providerID": "acme", "modelID": "last"}], "variant": {"acme/last": "xhigh"}}))
        with self.answers("other/m-2", "") as ask:
            self.z.choose_opencode_model()
        self.assertTrue(ask.call_args_list[1].args[0].endswith("nothing: its default)?"))
        self.assertEqual(json.loads(self.config.read_text()), {"model": "other/m-2"})
        self.config.unlink()
        with self.answers("other/m-2", "low"):
            self.z.choose_opencode_model()
        self.assertEqual(json.loads(self.config.read_text()), {"model": "other/m-2", "variant": "low"})

    def test_without_an_answer_or_a_terminal_nothing_is_guessed_and_the_coordinator_does_not_open(self):
        self.last_used(json.dumps({"recent": [{"providerID": "acme", "modelID": "last"}]}))
        with self.installed("opencode"):  # no terminal: the model the TUI last used is offered, never taken
            self.dies(lambda: self.z.cmd_coordinator([]), f"no model is set for OpenCode in {self.config}",
                      "Run `bin/hw coordinator` in a terminal and answer its question, or `bin/hw configure --model <provider/model>`")
        for state in ("", "not json", json.dumps({"recent": []}), json.dumps({"recent": [{"providerID": "a b", "modelID": "x"}]})):
            self.last_used(state)
            with self.answers(""):  # Enter with nothing to accept
                self.dies(self.z.choose_opencode_model, "a model is needed: OpenCode's server has no default that is yours. "
                          "`opencode models` lists them; run `bin/hw coordinator` again and type one (provider/model).")
        with self.answers("nemotron"):
            self.dies(self.z.choose_opencode_model, "an OpenCode model is written provider/model, not 'nemotron'")
        self.assertFalse(self.config.exists())
        self.assertEqual((self.processes, self.tui), ([], []))

    def test_the_answer_opens_the_coordinator_on_that_model(self):
        with self.installed("opencode"), self.answers("acme/m-9", "high"), self.assertRaises(SystemExit):
            self.z.cmd_coordinator([])
        self.assertIn("starting the coordinator on acme/m-9 (high)", self.output.getvalue())
        created = next(body for method, path, body in self.server.calls if (method, path) == ("POST", "/session"))
        self.assertEqual(created["model"], {"providerID": "acme", "id": "m-9", "variant": "high"})


class KitTests(CoordinatorCase):
    def test_a_model_without_a_variant_is_written_alone_and_no_model_is_refused(self):
        (self.profile / "config" / "opencode.json").write_text(json.dumps({"model": "acme/m-1"}))
        config = json.loads((self.z.coordinator_kit()["config_dir"] / "opencode.json").read_text())
        self.assertEqual((config["model"], "agents" in config), ("acme/m-1", False))
        (self.profile / "config" / "opencode.json").write_text("{}")
        self.dies(self.z.coordinator_kit, "no model is set for OpenCode")

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
        for command in ("bin/hw *", f"{HOME}/bin/hw *", "git status *", "herdr agent *", "hw-pretool-canary *"):
            self.assertIn(command, allowed)
        claude = json.loads((HOME / ".claude" / "settings.json").read_text())["permissions"]["allow"]
        self.assertEqual(len([r for r in allowed if not r.startswith("/") and "canary" not in r]), len(claude))  # one list for both
        plugin = (kit["config_dir"] / "plugins" / "hw-pretool" / "index.js").read_text()
        self.assertIn(json.dumps(str(HOME / "bin" / "hw-pretool")), plugin)
        self.assertIn(json.dumps({"HW_PROFILE_DIR": str(self.profile)}), plugin)
        self.assertEqual(kit["server"]["log"], kit["config_dir"] / "server.log")

    def test_without_a_profile_nothing_is_written_where_onboarding_will_create_it(self):
        (self.profile / "profile.md").unlink()
        before = sorted(p for p in self.profile.rglob("*"))
        kit = self.z.coordinator_kit()
        self.addCleanup(self.z.shutil.rmtree, kit["record"].parent, True)
        self.assertNotIn(self.profile, kit["config_dir"].parents)
        self.assertEqual(sorted(p for p in self.profile.rglob("*")), before)
        self.assertTrue((kit["config_dir"] / "plugins" / "hw-pretool" / "index.js").is_file())


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
        self.assertEqual(process.env["HW_COORDINATOR"], str(self.record))
        created = next(body for method, path, body in self.server.calls if (method, path) == ("POST", "/session"))
        self.assertEqual(created, {"location": {"directory": str(HOME)}, "title": "hw",
                                   "model": {"providerID": "acme", "id": "base", "variant": "xhigh"}})
        self.assertRegex(self.server.prompts()[0], r"exactly this command: hw-pretool-canary [0-9a-f]{8}\n")
        record = json.loads(self.record.read_text())
        self.assertEqual({k: record[k] for k in ("runtime", "session", "server", "server_pid", "guard", "model", "variant")},
                         {"runtime": "opencode", "session": "ses_test1", "server_pid": os.getpid(), "guard": "live",
                          "server": {"url": kit["server"]["url"], "password": kit["server"]["password"]},
                          "model": "acme/base", "variant": "xhigh"})
        self.assertEqual(stat.S_IMODE(self.record.stat().st_mode), 0o600)  # it holds the server's password

    def test_a_session_whose_guard_is_not_proven_is_not_recorded(self):
        for guard, up, model, why in (("dead", True, None, "was not denied (status completed): the coordinator guard (bin/hw-pretool) is not live"),
                                      ("silent", True, None, "the model made no canary call, so the coordinator guard (bin/hw-pretool) is not proven live"),
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

    def test_a_live_coordinator_of_the_code_before_the_rename_stops_it_before_anything_is_renamed_or_started(self):
        cache = self.profile / ".cache"
        cache.mkdir(exist_ok=True)
        (cache / "zed.lock").write_text(json.dumps({"pane": None, "agent_session": "ses_old", "pid": os.getpid()}))
        (self.profile / "db" / "hw.db").rename(self.profile / "db" / "zed.db")
        with self.installed("opencode"):
            self.dies(lambda: self.z.cmd_coordinator([]), f"OLD COORDINATOR STILL RUNNING: {cache / 'zed.lock'} is held by a live session")
        self.assertEqual((self.processes, self.tui), ([], []))
        self.assertTrue((cache / "zed.lock").exists())
        self.assertFalse((self.profile / "db" / "hw.db").exists())

    def test_a_second_coordinator_is_refused_while_one_is_open_and_the_first_keeps_its_record(self):
        """S338: a second run overwrote the record of the open, lock-holding session and deleted it on exit."""
        first = json.dumps({"session": "ses_first", "server_pid": os.getpid(), "guard": "live", "started_at": "earlier"})
        self.record.parent.mkdir(exist_ok=True)
        self.record.write_text(first)
        with self.installed("opencode"):
            self.dies(lambda: self.z.cmd_coordinator([]), "a coordinator is already open (session ses_first", "close it first")
        self.assertEqual((self.processes, self.tui), ([], []))
        self.assertEqual(self.record.read_text(), first)

    def test_a_record_left_by_a_coordinator_that_is_gone_does_not_block_and_another_runs_record_is_not_deleted(self):
        self.record.parent.mkdir(exist_ok=True)
        self.record.write_text(json.dumps({"session": "ses_gone", "server_pid": 2 ** 22 + 12345, "guard": "live"}))  # no such process
        taken_over = json.dumps({"session": "ses_other", "server_pid": 1, "guard": "live"})
        self.z.subprocess.call.side_effect = lambda *a, **k: self.record.write_text(taken_over) and 3  # while the TUI ran
        with self.installed("opencode"), self.assertRaises(SystemExit):
            self.z.cmd_coordinator([])
        self.assertEqual(len(self.processes), 1)  # it opened
        self.assertEqual(self.record.read_text(), taken_over)  # not this run's record: left alone

    def test_a_signal_during_the_canary_turn_stops_the_server_just_started(self):
        self.server = lambda *a, **k: (_ for _ in ()).throw(SystemExit(129))  # SIGHUP's handler, mid-request
        with self.installed("opencode"), self.assertRaises(SystemExit):
            self.z.cmd_coordinator([])
        self.assertTrue(self.processes[0].stopped)
        self.assertEqual(self.tui, [])

    def test_without_a_profile_the_temporary_kit_is_removed_when_the_session_ends(self):
        (self.profile / "profile.md").unlink()
        made = []
        real_kit = self.z.coordinator_kit
        with patch.object(self.z, "coordinator_kit", side_effect=lambda: made.append(real_kit()) or made[-1]), \
                self.installed("opencode"), self.assertRaises(SystemExit):
            self.z.cmd_coordinator([])
        self.assertIsNotNone(made[0]["temporary"])
        self.assertFalse(made[0]["temporary"].exists())

    def test_a_session_whose_guard_is_not_live_is_never_opened(self):
        self.server.guard = "dead"
        with self.installed("opencode"):
            self.dies(lambda: self.z.cmd_coordinator([]), "the coordinator was not opened:", "the coordinator guard (bin/hw-pretool) is not live")
        self.assertEqual(self.tui, [])
        self.assertTrue(self.processes[0].stopped)

    def test_it_is_for_an_opencode_install_and_never_for_a_worker(self):
        self.settings({"runtime": "claude"})
        self.dies(lambda: self.z.cmd_coordinator([]), "this install runs on Claude Code: start `claude` in")
        self.settings({"runtime": "opencode"})
        with patch.dict(os.environ, {"HW_SESSION": "S7"}):
            self.dies(lambda: self.z.cmd_coordinator([]), "this is worker session S7, not Herdwright")
        with self.installed():
            self.dies(lambda: self.z.cmd_coordinator([]), "`opencode` is not installed")
        self.assertEqual((self.processes, self.tui), ([], []))


class StartTests(CoordinatorCase):
    def start(self, env=None, herdr_session=None, branch="profile/me", watcher=None, comes_up=True):
        """`bin/hw start` as the shell tool of an OpenCode session runs it: `env` is what that shell carries.
        `watcher`: the pid of the watcher already running; `self.armed` counts the ones `start` started (`comes_up`:
        whether one it starts gets going)."""
        env = {"OPENCODE_SESSION_ID": "ses_test1", "HW_COORDINATOR": str(self.record)} if env is None else env
        real_git = self.z.hwlib.git

        def git(path, *args, **kwargs):
            if args == ("branch", "--show-current"):
                return subprocess.CompletedProcess(args, 0, branch + "\n", "")
            return real_git(path, *args, **kwargs)

        self.output.seek(0)
        self.output.truncate(0)
        # ZED_: HW_ before the rename; `start` names those still set, also to a new user
        clean = {k: v for k, v in os.environ.items() if not k.startswith(("OPENCODE_", "HW_", "ZED_", "HERDR_", "CLAUDE"))}
        with patch.dict(os.environ, clean | env, clear=True), patch.object(self.z, "cmd_lock") as lock, \
                patch.object(self.z, "holds_lock", return_value=True), patch.object(self.z, "live_agents", return_value={}), \
                patch.object(self.z, "watcher_alive", return_value=watcher), patch.object(self.z.hwlib, "git", side_effect=git), \
                patch.object(self.z, "arm_watcher", side_effect=lambda: self.armed.append(1) or (4321 if comes_up else None)), \
                patch.object(self.z, "pane_session", return_value=herdr_session), \
                patch.object(self.z, "script_findings", return_value=[]):
            self.z.cmd_start([])
        return lock

    armed = []

    def setUp(self):
        super().setUp()
        self.armed = []

    def test_the_watchers_log_is_cut_back_when_it_has_grown_and_keeps_its_last_words(self):
        log = self.profile / ".cache" / "watch.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("x" * 100 + "é\nwhy the last watcher ended\n")
        with patch.object(self.z, "WATCH_LOG_BYTES", 58), patch.object(self.z.subprocess, "Popen") as popen, \
                patch.object(self.z, "watcher_alive", return_value=4321):
            self.assertEqual(self.z.arm_watcher(), 4321)
            self.assertEqual(log.read_text(), "why the last watcher ended\n")  # the whole lines in the last half of the limit
            self.assertEqual(popen.call_args.args[0][-2:], ["watch", "--daemon"])
            log.write_text("short\n")
            self.z.arm_watcher()
            self.assertEqual(log.read_text(), "short\n")

    def test_start_starts_the_long_lived_watcher_when_none_runs_and_says_so(self):
        """OpenCode has no Stop hook to re-arm a watcher: `start` is what starts it, with or without tracked work."""
        self.proven()
        self.start()
        self.assertEqual(self.armed, [1])
        self.assertIn("watcher: was NOT RUNNING; started now, pid 4321", self.output.getvalue())
        self.start(watcher=77)
        self.assertEqual(self.armed, [1])  # one is enough
        self.assertIn("watcher: alive, pid 77", self.output.getvalue())
        self.start(comes_up=False)
        self.assertIn(f"watcher: NOT RUNNING and it did not start: read {self.profile / '.cache' / 'watch.log'}", self.output.getvalue())

    def proven(self, **changes):
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_text(json.dumps({"session": "ses_test1", "server_pid": os.getpid(), "guard": "live",
                                           "started_at": "2026-11-11 10:00"} | changes))

    def test_start_refuses_an_opencode_session_the_coordinator_command_did_not_open(self):
        refusal = "was not opened by `bin/hw coordinator`, so its guard (bin/hw-pretool) is not proven"
        self.dies(lambda: self.start(env={"OPENCODE_SESSION_ID": "ses_test1"}), refusal)  # plain `opencode`
        self.dies(self.start, refusal)  # the record is gone (its server stopped)
        self.proven(session="ses_other")
        self.dies(self.start, refusal)  # another session's proof
        self.proven(guard="unknown")
        self.dies(self.start, refusal)
        self.proven(server_pid=2 ** 22 + 12345)  # no such process
        self.dies(self.start, refusal)
        self.assertFalse((self.profile / ".cache" / "hw.lock").exists())

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
            self.dies(lambda: self.start(env={"OPENCODE_SESSION_ID": "ses_test1", "HW_COORDINATOR": str(self.record),
                                              "HERDR_PANE_ID": "w1:p1"}, herdr_session="herdr-knows-another-id"),
                      "is ses_test1 to OpenCode but herdr-knows-another-id to herdr")

    def test_a_new_user_is_sent_to_onboarding_and_nothing_else_happens(self):
        (self.profile / "profile.md").unlink()
        self.proven()
        lock = self.start()
        lock.assert_not_called()
        self.assertEqual(self.output.getvalue().strip(),
                         f"NEW USER: there is no profile at {self.profile}. Run the `onboarding` skill and do nothing else until "
                         "it is done. Then tell the user to quit and run `bin/hw coordinator` again.")

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


class WakeTests(CoordinatorCase):
    """What the long-lived watcher does with an event (tests/test_e2e_wake.py runs it whole; tests/smoke_opencode.py on the real runtime)."""
    RECORD = {"session": "ses_test1", "server": {"url": "http://127.0.0.1:1", "password": "x"}}

    def test_the_servers_message_id_is_the_receipt(self):
        receipt = self.z.wake_coordinator(self.RECORD, "w1:p1", ["[E1] S1 asks", "[E2] S2 is gone"])
        (method, path, body), = self.server.calls
        self.assertEqual((method, path), ("POST", "/session/ses_test1/prompt"))
        self.assertEqual(body["text"].splitlines(), [self.z.WAKE_HEADING, "[E1] S1 asks", "[E2] S2 is gone"])
        self.assertEqual((receipt["via"], receipt["message"]), ("api", "msg_1"))
        self.assertEqual([c for c in self.herdr_calls if c[0] == "herdr"], [])  # nothing typed

    def test_a_server_that_fails_leaves_the_prompt_to_herdr_and_both_failing_is_said(self):
        self.server.up = False
        receipt = self.z.wake_coordinator(self.RECORD, "w1:p1", ["[E1] S1 asks"])
        self.assertEqual((receipt["via"], receipt["api_error"]), ("herdr", "ConnectionRefusedError: connection refused"))
        self.assertEqual(self.herdr_calls[-1], ["herdr", "agent", "prompt", "w1:p1", f"{self.z.WAKE_HEADING}\n[E1] S1 asks"])
        failed = subprocess.CompletedProcess([], 1, "", "agent_not_found")
        with patch.object(self.z, "run", return_value=failed):
            receipt = self.z.wake_coordinator(self.RECORD, "w1:p1", ["[E1] S1 asks"])
        self.assertEqual((receipt["via"], receipt["error"]), (None, "ConnectionRefusedError: connection refused; herdr: agent_not_found"))
        self.assertIn("this session is in no herdr pane", self.z.wake_coordinator(self.RECORD, None, ["[E1] S1 asks"])["error"])

    def test_a_receipt_is_kept_on_its_events_and_an_event_is_shown_again_only_after_a_while(self):
        shown = self.z.queue_add(["S1 asks", "S2 is gone"])
        self.z.queue_receipt(shown[:1], {"via": "api", "message": "msg_9"})
        first, second = self.z.queue_pending()
        self.assertEqual((first["wake"], "wake" in second), ({"via": "api", "message": "msg_9"}, False))
        self.assertEqual(self.z.queue_represent(self.z.REPRESENT_SECONDS), [])  # fresh: the long-lived watcher leaves them
        with self.z.wake_queue() as q:
            q["events"][1]["shown_at"] -= self.z.REPRESENT_SECONDS + 1
        again, = self.z.queue_represent(self.z.REPRESENT_SECONDS)
        self.assertRegex(again, r"^\[E2\] \(again, un-acked since .*\) S2 is gone$")
        self.assertEqual(self.z.queue_represent(self.z.REPRESENT_SECONDS), [])


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

    def test_a_working_session_is_not_interrupted_and_its_answer_waits_for_it_in_the_record(self):
        self.state.stop()
        with patch.object(self.z, "session_agent_state", return_value="working"):
            self.z.cmd_relay([f"Q{self.q1}", "use port 9196"])
            self.mock_prompt_worker.assert_not_called()
            q = self.question(self.q1)
            self.assertEqual((q["status"], q["answer"], q["delivered_at"]), ("answered", "use port 9196", None))  # for it to read
            self.assertIn('"Which port?": "use port 9196"', self.brief.read_text())
            self.assertIn(f"S{self.sid} is working, so it was not prompted with Q{self.q1}: it reads them with `hw-employee answers`",
                          self.output.getvalue())
            self.z.cmd_relay([f"Q{self.q2}", "No.", "--redirect"])  # meant to interrupt: prompted at once
        self.state.start()
        self.mock_prompt_worker.assert_called_once()
        self.assertIsNotNone(self.question(self.q2)["delivered_at"])

    def test_an_answer_prompted_to_an_idle_worker_counts_as_given(self):
        self.z.cmd_relay([f"Q{self.q1}", "use port 9196"])
        self.assertIsNotNone(self.question(self.q1)["delivered_at"])
        self.assertEqual(self.z.hwlib.take_answers(self.conn, self.sid, "now"), [])  # nothing left for the watcher to send again

    def second_worker(self):
        """A second session with a question of its own (a copy of the first, in another pane); returns (session, question)."""
        row = dict(self.session(self.sid)) | {"id": None, "agent": "build-two", "pane_id": "pane-two"}
        other = self.conn.execute(f"INSERT INTO sessions({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values())).lastrowid
        q = self.z.add_question(self.conn, 20, other, "Which region?")
        self.conn.commit()
        return other, q

    def test_two_workers_are_both_checked_before_either_is_prompted(self):
        """S338: with the second worker not promptable, the first had already received its answer and nothing was recorded."""
        other, q3 = self.second_worker()
        self.state.stop()
        with patch.object(self.z, "session_agent_state", side_effect=lambda s: "gone" if s["id"] == other else "idle"):
            self.dies(lambda: self.z.cmd_relay([f"Q{self.q1}", "use port 9196", f"Q{q3}", "eu"]),
                      f"S{other} has no live agent", "Nothing was sent and nothing recorded.")
        self.mock_prompt_worker.assert_not_called()
        self.assertEqual([self.question(q)["status"] for q in (self.q1, q3)], ["open", "open"])
        self.assertNotIn("9196", self.brief.read_text())
        with patch.object(self.z, "session_agent_state", side_effect=lambda s: "working" if s["id"] == other else "idle"):
            self.z.cmd_relay([f"Q{self.q1}", "use port 9196", f"Q{q3}", "eu"])  # one idle, one working: only the idle one is prompted
        self.state.start()
        self.assertEqual([c.args[0] for c in self.mock_prompt_worker.call_args_list], ["build-t20"])
        self.assertEqual([(self.question(q)["status"], self.question(q)["delivered_at"] is None) for q in (self.q1, q3)],
                         [("answered", False), ("answered", True)])

    def test_a_prompt_that_fails_for_the_second_worker_leaves_the_first_recorded_and_not_prompted_again(self):
        other, q3 = self.second_worker()
        self.mock_prompt_worker.side_effect = lambda agent, text: (agent != "build-two", "agent_prompt_stalled")
        self.dies(lambda: self.z.cmd_relay([f"Q{self.q1}", "use port 9196", f"Q{q3}", "eu"]), f"prompt to S{other} failed: agent_prompt_stalled",
                  f"recorded before this failure: Q{self.q1}. Not sent and not recorded: Q{q3}.")  # S341: what to run again
        self.assertEqual([(self.question(q)["status"], self.question(q)["answer"]) for q in (self.q1, q3)], [("answered", "use port 9196"), ("open", None)])
        self.assertIn('"Which port?": "use port 9196"', self.brief.read_text())
        self.mock_prompt_worker.side_effect = None
        self.mock_prompt_worker.reset_mock()
        self.z.cmd_relay([f"Q{q3}", "eu"])  # the rest, again: the first worker hears nothing twice
        self.assertEqual([c.args[0] for c in self.mock_prompt_worker.call_args_list], ["build-two"])
        self.assertEqual(self.question(q3)["answer"], "eu")

    def test_an_answer_of_several_lines_stays_one_item_in_the_brief(self):
        self.z.cmd_relay([f"Q{self.q1}", "use port 9196\n\nand keep 9000 as a fallback"])
        self.assertEqual(self.question(self.q1)["answer"], "use port 9196\n\nand keep 9000 as a fallback")  # the record is verbatim
        self.assertIn('"Which port?": "use port 9196 and keep 9000 as a fallback"\n\n## Plan', self.brief.read_text())

    def test_a_prompt_that_fails_leaves_the_question_open(self):
        self.mock_prompt_worker.return_value = (False, "agent_prompt_stalled")
        self.dies(lambda: self.z.cmd_relay([f"Q{self.q1}", "use port 9196"]), "failed: agent_prompt_stalled")
        self.assertEqual(self.question(self.q1)["status"], "open")
        self.assertNotIn("9196", self.brief.read_text())

    def test_a_question_no_session_asked_is_only_recorded_and_bad_input_changes_nothing(self):
        own = self.z.add_question(self.conn, None, None, "Herdwright's own question")
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
