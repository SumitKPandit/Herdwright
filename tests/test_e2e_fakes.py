"""The fakes themselves (tests/fakes/): what the fake herdr answers and what a scripted agent does, without `bin/zed`.
These pin the behaviour the end-to-end tests in test_e2e_loop.py stand on, and the parts of the fakes they do not use
yet (a prompt left unsent in the input box, an agent CLI that quits)."""
import json
import subprocess
import unittest

from _e2e import World


class FakeCase(unittest.TestCase):
    def setUp(self):
        self.w = World(self)

    def herdr(self, *args):
        return subprocess.run(["herdr", *args], env=self.w.env(), capture_output=True, text=True)

    def result(self, *args):
        r = self.herdr(*args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["result"]

    def agent(self, name="worker", **script):
        """Start an agent in a new workspace (its pane: `self.pane`; the start command: `self.started`). Returns the workspace."""
        self.w.script(default=script)
        ws = self.result("workspace", "create", "--cwd", str(self.w.root), "--label", "a task", "--no-focus", "--env", "ZED_HAT=build")
        self.pane = ws["root_pane"]["pane_id"]
        self.started = self.herdr("agent", "start", name, "--kind", "claude", "--pane", self.pane, "--timeout", "60000",
                                  "--", "-n", name, "--model", "some-model")
        return ws

    def status(self, name="worker"):
        return self.result("agent", "get", name)["agent"]["agent_status"]


class Herdr(FakeCase):
    def test_a_started_agent_is_listed_with_its_pane_session_and_launch_flags(self):
        ws = self.agent()
        self.assertEqual(self.started.returncode, 0, self.started.stderr)
        listed = {a["pane_id"]: a for a in self.result("agent", "list")["agents"]}
        mine = listed[self.pane]
        self.assertEqual((mine["agent"], mine["agent_status"], mine["workspace_id"]), ("claude", "idle", ws["workspace"]["workspace_id"]))
        self.assertEqual(self.result("pane", "get", self.pane)["pane"]["agent_session"]["value"], mine["agent_session"]["value"])
        self.assertIn(self.pane, [p["pane_id"] for p in self.result("pane", "list")["panes"]])
        launch = self.w.agent("worker")["launch"]
        self.assertEqual((launch["name"], launch["model"], launch["runtime"]), ("worker", "some-model", "claude"))

    def test_a_closed_workspace_takes_its_pane_and_agent_with_it(self):
        ws = self.agent()
        self.result("workspace", "close", ws["workspace"]["workspace_id"])
        self.assertIn("pane_not_found", self.herdr("pane", "get", self.pane).stderr)
        self.assertIn("agent_not_found", self.herdr("agent", "get", "worker").stderr)
        self.assertIn("workspace_not_found", self.herdr("workspace", "close", ws["workspace"]["workspace_id"]).stderr)

    def test_an_agent_that_opens_on_a_dialog_is_not_ready_until_it_is_answered(self):
        self.agent(start={"dialog": "Trust this folder?"})
        self.assertNotEqual(self.started.returncode, 0)
        self.assertIn("agent_not_ready", self.started.stderr)
        self.assertEqual(self.status(), "blocked")
        self.assertIn("Trust this folder?", self.herdr("agent", "read", "worker", "--source", "visible").stdout)
        self.assertNotEqual(self.herdr("agent", "wait", "worker", "--until", "idle", "--timeout", "1000").returncode, 0)
        self.result("agent", "send-keys", "worker", "down", "enter")
        self.assertEqual(self.herdr("agent", "wait", "worker", "--until", "idle", "--timeout", "1000").returncode, 0)


class Agent(FakeCase):
    def prompt(self, text):
        return self.herdr("agent", "prompt", "worker", text, "--wait", "--until", "working", "--until", "blocked", "--timeout", "30000")

    def test_a_turn_runs_its_acts_and_ends_idle(self):
        self.agent(turns=[[{"say": "On it."}, {"write": "out/note.txt", "text": "done\n"}, {"git": ["--version"]}]])
        before = self.result("agent", "get", "worker")["agent"]["state_change_seq"]
        self.assertEqual(self.prompt("Do the thing").returncode, 0)
        agent = self.result("agent", "get", "worker")["agent"]
        self.assertEqual((agent["agent_status"], agent["state_change_seq"]), ("idle", before + 2))  # working, then idle
        self.assertEqual((self.w.root / "out" / "note.txt").read_text(), "done\n")
        self.assertEqual(self.w.agent("worker")["results"][0]["rc"], 0)
        screen = self.herdr("agent", "read", "worker", "--source", "recent-unwrapped", "--lines", "150").stdout
        self.assertIn("❯ Do the thing\n⏺ On it.\n", screen)
        self.assertIn("✻ Worked for 1s\n", screen)

    def test_a_prompt_left_in_the_input_box_is_sent_by_enter_once(self):
        self.agent(swallow=[1])
        r = self.prompt("Read your brief")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("agent_prompt_stalled", r.stderr)
        self.assertEqual(self.w.agent("worker")["prompts"], [])
        self.result("agent", "send-keys", "worker", "enter")
        self.result("agent", "send-keys", "worker", "enter")  # an empty box: nothing more is sent
        self.assertEqual(self.w.agent("worker")["prompts"], ["Read your brief"])

    def test_a_paused_turn_goes_on_when_time_passes_and_takes_queued_prompts_in_order(self):
        self.agent(turns=[[{"pause": True}, {"say": "First done."}], [{"say": "Second done."}]])
        self.prompt("First")
        self.assertEqual(self.status(), "working")
        self.prompt("Second")  # arrives mid-turn: waits
        self.assertEqual(self.w.agent("worker")["queued"], ["Second"])
        self.w.advance("worker")
        agent = self.w.agent("worker")
        self.assertEqual((agent["status"], agent["prompts"], agent["queued"]), ("idle", ["First", "Second"], []))
        self.assertEqual([line for line in agent["screen"] if "done" in line], ["⏺ First done.", "⏺ Second done."])

    def test_an_agent_cli_that_quits_leaves_its_pane_without_an_agent(self):
        self.agent(turns=[[{"exit": True}]])
        self.prompt("Go away")
        self.assertIsNone(self.w.agent("worker"))
        self.assertEqual([a for a in self.result("agent", "list")["agents"] if a["pane_id"] == self.pane], [])
        self.assertNotIn("agent_session", self.result("pane", "get", self.pane)["pane"])  # the pane itself is still there


class Gh(FakeCase):
    def test_only_a_listed_pull_request_can_be_read(self):
        url = "https://github.com/acme/demo/pull/7"
        (self.w.herdr / "prs.json").write_text(json.dumps({url: {"state": "MERGED", "reviews": [], "comments": []}}))
        view = ["gh", "pr", "view", url, "--json", "state,reviews,comments,statusCheckRollup"]
        r = subprocess.run(view, env=self.w.env(), capture_output=True, text=True)
        self.assertEqual(json.loads(r.stdout)["state"], "MERGED")
        r = subprocess.run(["gh", "pr", "list"], env=self.w.env(), capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
