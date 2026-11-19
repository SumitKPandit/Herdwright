"""`bin/hw configure`: how the user's word about the model, its effort or variant and the number of sessions becomes
the profile's config, and that the next worker starts with it. Herdwright ships none of these values."""
import json
import unittest
from unittest.mock import patch

from _e2e import MODELS, LoopCase, per_runtime
from test_hw_assign import HOME, AssignCase, FakeOpenCodeServer

TEMPLATE = HOME / "profile.template" / "config"
MODEL_KEYS = {"model", "effort", "variant", "effort_by_kind"}


def keys_in(value):
    """Every key of a JSON value, at any depth."""
    if isinstance(value, dict):
        return set(value) | {key for inner in value.values() for key in keys_in(inner)}
    return {key for inner in value for key in keys_in(inner)} if isinstance(value, list) else set()


class TemplateTests(unittest.TestCase):
    def test_the_template_ships_no_model_effort_or_variant(self):
        for path in sorted(TEMPLATE.glob("*.json")):
            self.assertEqual(keys_in(json.loads(path.read_text())) & MODEL_KEYS, set(), path.name)
        self.assertEqual(json.loads((TEMPLATE / "claude.json").read_text()), {"max_running": 3})
        self.assertEqual(json.loads((TEMPLATE / "opencode.json").read_text()), {})

    def test_no_model_is_written_into_the_code_as_a_fallback(self):
        """The check the test above cannot make: a default that lives in bin/ instead of the template."""
        models = ["claude-opus", "claude-sonnet", "claude-haiku", "claude-fable", "nemotron", "gpt-"]
        for path in [HOME / "bin" / "hw", HOME / "bin" / "hw-setup", *sorted((HOME / "bin" / "lib").glob("*.py"))]:
            text = path.read_text()
            self.assertEqual([m for m in models if m in text], [], path.name)


class ConfigureCase(AssignCase):
    RUNTIME = "claude"

    def setUp(self):
        super().setUp()
        self.config = self.profile / "config" / f"{self.RUNTIME}.json"
        for name in ("claude.json", "opencode.json"):
            (self.profile / "config" / name).unlink()
        (self.profile / "config" / "settings.json").write_text(json.dumps({"runtime": self.RUNTIME}))

    def configure(self, *args):
        self.output.seek(0)
        self.output.truncate(0)
        self.z.cmd_configure(list(args))
        return self.output.getvalue().splitlines()

    def refused(self, text, *args):
        with self.assertRaises(SystemExit) as e:
            self.configure(*args)
        self.assertEqual(e.exception.code, 1)
        self.assertIn(text, self.output.getvalue())

    def stored(self):
        return json.loads(self.config.read_text()) if self.config.exists() else None

    def claude_settings(self):
        """Herdwright's own `.claude/settings.local.json`, in a throwaway place: what a coordinator's `claude` reads at its start."""
        path = self.root / "home" / ".claude" / "settings.local.json"
        patcher = patch.object(self.z, "COORDINATOR_CLAUDE_SETTINGS", path)
        patcher.start()
        self.addCleanup(patcher.stop)
        return path

    def launch(self):
        """The arguments the runtime's CLI was started with for the last worker."""
        start = [c for c in self.herdr_calls if c[:3] == ["herdr", "agent", "start"]][-1]
        return start[start.index("--") + 1:]


class OnClaudeCode(ConfigureCase):
    def test_with_nothing_set_a_worker_is_started_without_model_or_effort_so_claude_codes_own_default_applies(self):
        self.assertEqual(self.configure(), ["runtime       claude (Claude Code)", "model         not set: Claude Code's own default applies",
                                            "effort        not set: Claude Code's own default applies", "max sessions  not set: no limit",
                                            f"stored in     {self.config}"])
        sid = self.spawn("build")
        self.assertNotIn("--model", self.launch())
        self.assertNotIn("--effort", self.launch())
        self.assertEqual((self.session(sid)["model"], self.session(sid)["effort"]), (None, None))
        self.assertIn("model default effort default (nothing set in claude.json: Claude Code's own default)",
                      self.conn.execute("SELECT text FROM events WHERE kind='spawn'").fetchone()[0])
        self.assertIsNone(self.stored())  # showing and spawning write nothing

    def test_what_the_user_asked_for_is_stored_confirmed_and_used_by_every_later_worker(self):
        said = self.configure("--model", "opus", "--effort", "high", "--max-sessions", "4")
        self.assertEqual(said[1:4], ["model         opus", "effort        high",
                                     "max sessions  4 open Claude Code sessions at once, Herdwright's and yours included"])
        self.assertEqual(self.stored(), {"model": "opus", "effort": "high", "max_running": 4})
        for effort in ("high", "high", "low"):  # it holds for every later worker, until the user changes it
            if effort == "low":
                self.configure("--effort", "low")  # one thing changes, the rest stays
            sid = self.spawn("build")
            launch = self.launch()
            self.assertEqual((launch[launch.index("--model") + 1], launch[launch.index("--effort") + 1]), ("opus", effort))
            self.assertEqual((self.session(sid)["model"], self.session(sid)["effort"]), ("opus", effort))
            self.finish(sid)
        self.assertEqual(self.stored(), {"model": "opus", "effort": "low", "max_running": 4})

    def test_clearing_a_choice_goes_back_to_claude_codes_own_default(self):
        self.configure("--model", "opus", "--effort", "high", "--max-sessions", "2")
        said = self.configure("--clear", "model", "--clear", "max-sessions")
        self.assertEqual(said[1:4], ["model         not set: Claude Code's own default applies", "effort        high", "max sessions  not set: no limit"])
        self.assertEqual(self.stored(), {"effort": "high"})
        self.spawn("build")
        self.assertNotIn("--model", self.launch())
        self.assertEqual(self.launch()[self.launch().index("--effort") + 1], "high")

    def test_what_cannot_be_a_choice_is_refused_and_nothing_is_stored(self):
        for text, args in (("'extreme' is not an effort of Claude Code: low, medium, high, xhigh, max", ("--effort", "extreme")),
                           ("Claude Code has no variant: its choices are --model, --effort and --max-sessions", ("--variant", "high")),
                           ("--max-sessions takes a number of sessions, 1 or more, not '0'", ("--max-sessions", "0")),
                           ("--max-sessions takes a number of sessions, 1 or more, not 'many'", ("--max-sessions", "many")),
                           ("usage: hw configure [--model <model>]", ("--model",)),
                           ("usage: hw configure [--model <model>]", ("--colour", "red")),
                           ("--clear takes one of model, effort, variant, max-sessions, not 'runtime'", ("--clear", "runtime")),
                           ("model is given twice in one command: say once whether to set it or to clear it",
                            ("--model", "opus", "--clear", "model")),
                           ("model is given twice in one command", ("--clear", "model", "--model", "opus")),
                           ("effort is given twice in one command", ("--effort", "low", "--effort", "high")),
                           ("the model is a word, not ' '", ("--model", " "))):
            self.refused(text, *args)
            self.assertIsNone(self.stored(), args)

    def test_each_step_runs_on_its_own_model_and_effort_and_what_it_lacks_is_what_holds_for_every_session(self):
        self.configure("--model", "opus", "--effort", "high")
        said = self.configure("--step", "plan", "--model", "fable", "--effort", "max")
        self.assertIn("step plan     model fable, effort max", said)
        self.configure("--step", "review", "--effort", "low")
        self.assertEqual(self.stored(), {"model": "opus", "effort": "high",
                                         "steps": {"plan": {"model": "fable", "effort": "max"}, "review": {"effort": "low"}}})
        for hat, model, effort in (("plan", "fable", "max"), ("build", "opus", "high"), ("review", "opus", "low"), ("qa", "opus", "high")):
            sid = self.spawn(hat)
            launch = self.launch()
            self.assertEqual((launch[launch.index("--model") + 1], launch[launch.index("--effort") + 1]), (model, effort), hat)
            self.assertEqual((self.session(sid)["model"], self.session(sid)["effort"]), (model, effort), hat)
            self.finish(sid)
        self.assertIn("(claude.json, step plan)", self.conn.execute("SELECT text FROM events WHERE kind='spawn' ORDER BY id").fetchone()[0])

    def test_a_step_with_nothing_set_anywhere_starts_without_model_or_effort(self):
        self.configure("--step", "plan", "--model", "fable")
        self.spawn("build")
        self.assertEqual([a for a in self.launch() if a in ("--model", "--effort")], [])

    def test_clearing_a_steps_choice_goes_back_to_what_holds_for_every_session(self):
        self.configure("--model", "opus", "--step", "qa")  # a model given with --step is the step's
        self.assertEqual(self.stored(), {"steps": {"qa": {"model": "opus"}}})
        self.configure("--step", "qa", "--clear", "model")
        self.assertEqual(self.stored(), {})
        self.assertNotIn("step qa", " ".join(self.configure()))

    def test_what_cannot_be_a_steps_choice_is_refused(self):
        for text, args in (("--step takes one of plan, build, qa, review, coordinator, not 'verify'", ("--step", "verify", "--model", "opus")),
                           ("--step takes one of plan, build, qa, review, coordinator, not 'once'", ("--step", "once", "--model", "opus")),
                           ("--step is given twice in one command: one step at a time", ("--step", "plan", "--step", "qa", "--model", "opus")),
                           ("--plan-approval holds for every build: it is not a step's (leave --step out)",
                            ("--step", "coordinator", "--plan-approval", "on")),
                           ("--max-sessions counts every session: it is not a step's", ("--step", "plan", "--max-sessions", "2")),
                           ("--step plan needs what to set or clear for it", ("--step", "plan")),
                           ("'extreme' is not an effort of Claude Code", ("--step", "plan", "--effort", "extreme")),
                           ("--plan-approval takes on or off, not 'maybe'", ("--plan-approval", "maybe"))):
            self.refused(text, *args)
            self.assertIsNone(self.stored(), args)

    def test_the_coordinators_own_choice_is_written_where_its_claude_reads_it_at_the_next_start(self):
        settings = self.claude_settings()
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}}))
        said = self.configure("--step", "coordinator", "--model", "opus", "--effort", "xhigh")
        self.assertIn("step coordinator model opus, effort xhigh", said)
        self.assertIn("the coordinator's own choice holds from its next start: a session that is running keeps its model", said[-1])
        self.assertEqual(json.loads(settings.read_text()), {"permissions": {"allow": ["Bash(ls:*)"]}, "model": "opus", "effortLevel": "xhigh"})
        self.spawn("build")  # the coordinator's choice is not a worker's
        self.assertEqual([a for a in self.launch() if a in ("--model", "--effort")], [])
        self.configure("--step", "coordinator", "--clear", "model", "--clear", "effort")
        self.assertEqual(json.loads(settings.read_text()), {"permissions": {"allow": ["Bash(ls:*)"]}})
        self.refused("Claude Code keeps the effort 'max' for one session only, never as a setting", "--step", "coordinator", "--effort", "max")

    def test_plan_approval_is_off_until_the_user_switches_it_on_and_off_again(self):
        import hwlib
        self.assertFalse(hwlib.plan_approval())
        said = self.configure("--plan-approval", "on")
        self.assertIn("plan approval on: a build starts only after you approved its plan", said)
        self.assertTrue(hwlib.plan_approval())
        self.assertEqual(json.loads((self.profile / "config" / "settings.json").read_text()), {"runtime": "claude", "plan_approval": True})
        self.assertNotIn("plan approval", " ".join(self.configure("--plan-approval", "off")))
        self.assertFalse(hwlib.plan_approval())

    def test_other_keys_of_the_file_stay(self):
        self.config.write_text(json.dumps({"effort_by_kind": {"design": "high"}, "max_running": 3}))
        self.configure("--model", "sonnet")
        self.assertEqual(self.stored(), {"effort_by_kind": {"design": "high"}, "max_running": 3, "model": "sonnet"})


class OnOpenCode(ConfigureCase):
    RUNTIME = "opencode"

    def setUp(self):
        super().setUp()
        self.server = FakeOpenCodeServer(self)
        patcher = patch.object(self.z, "opencode_api", side_effect=lambda *a, **k: self.server(*a, **k))
        patcher.start()
        self.addCleanup(patcher.stop)

    def created(self):
        return [body for method, path, body in self.server.calls if (method, path) == ("POST", "/session")][-1]

    def test_what_the_user_asked_for_is_stored_confirmed_and_used_by_the_next_worker(self):
        said = self.configure("--model", "acme/m-1", "--variant", "low", "--max-sessions", "2")
        self.assertEqual(said, ["runtime       opencode (OpenCode)", "model         acme/m-1", "variant       low",
                                "max sessions  2 open OpenCode sessions at once, Herdwright's and yours included", f"stored in     {self.config}"])
        self.assertEqual(self.stored(), {"model": "acme/m-1", "variant": "low", "max_running": 2})
        sid = self.spawn("build")
        self.assertEqual(self.created()["model"], {"providerID": "acme", "id": "m-1", "variant": "low"})
        self.assertEqual((self.session(sid)["model"], self.session(sid)["effort"]), ("acme/m-1", "low"))

    def test_each_step_runs_on_its_own_model_and_variant_and_what_it_lacks_is_what_holds_for_every_session(self):
        self.configure("--model", "acme/m-1", "--variant", "low")
        said = self.configure("--step", "plan", "--model", "acme/big", "--variant", "high")
        self.assertIn("step plan     model acme/big, variant high", said)
        self.configure("--step", "qa", "--variant", "minimal")
        self.assertEqual(self.stored()["steps"], {"plan": {"model": "acme/big", "variant": "high"}, "qa": {"variant": "minimal"}})
        for hat, model in (("plan", {"providerID": "acme", "id": "big", "variant": "high"}),
                           ("build", {"providerID": "acme", "id": "m-1", "variant": "low"}),
                           ("qa", {"providerID": "acme", "id": "m-1", "variant": "minimal"})):
            self.server = FakeOpenCodeServer(self)  # each worker has its own server
            sid = self.spawn(hat)
            self.assertEqual(self.created()["model"], model, hat)
            self.finish(sid)

    def test_the_coordinator_opens_on_its_own_model_and_variant(self):
        self.configure("--model", "acme/m-1", "--variant", "low")
        said = self.configure("--step", "coordinator", "--model", "acme/big")
        self.assertIn("the coordinator's own choice holds from its next start", said[-1])
        kit = self.z.coordinator_kit()
        self.assertEqual((kit["model"], kit["variant"]), ("acme/big", "low"))
        self.configure("--step", "coordinator", "--clear", "model")
        self.assertEqual(self.z.coordinator_kit()["model"], "acme/m-1")  # as before: the stored model

    def test_a_steps_variant_needs_a_model_from_somewhere(self):
        self.refused("no model is set for OpenCode", "--step", "plan", "--variant", "low")
        self.assertIsNone(self.stored())

    def test_a_cleared_variant_leaves_the_models_default_variant(self):
        self.configure("--model", "acme/m-1", "--variant", "low")
        self.assertEqual(self.configure("--clear", "variant")[2], "variant       not set: the model's default variant applies")
        self.spawn("build")
        self.assertEqual(self.created()["model"], {"providerID": "acme", "id": "m-1"})

    def test_opencode_needs_a_model_so_none_is_shown_as_not_set_and_it_cannot_be_cleared(self):
        self.assertEqual(self.configure()[1], "model         NOT SET: `bin/hw coordinator` asks for it, or --model <provider/model>")
        self.refused("no model is set for OpenCode", "--variant", "low")
        self.assertIsNone(self.stored())
        self.configure("--max-sessions", "2")  # the session limit does not depend on the model
        self.configure("--model", "acme/m-1")
        self.refused("OpenCode needs a model: set another one with --model <provider/model> instead of clearing it", "--clear", "model")
        self.refused("an OpenCode model is written provider/model, not 'opus'", "--model", "opus")
        self.refused("OpenCode has no effort: its choices are --model, --variant and --max-sessions", "--effort", "high")
        self.assertEqual(self.stored(), {"max_running": 2, "model": "acme/m-1"})


class StartSaysWhatWorkersRunOn(LoopCase):
    """The real command, as the assistant runs it in its session, on each runtime."""

    def test_start_names_the_choices_and_a_new_limit_shows_on_the_next_start(self):
        chosen = ", ".join(f"{key} {value}" for key, value in MODELS[self.RUNTIME].items())
        self.assertIn(f"workers: {chosen} · no session limit (change: bin/hw configure)", self.w.start().stdout.splitlines())
        said = self.w.hw("configure", "--max-sessions", "2").stdout
        self.assertIn("max sessions  2 open", said)
        self.assertIn(f"workers: {chosen} · at most 2 sessions open (change: bin/hw configure)", self.w.start().stdout.splitlines())

    def test_start_names_each_steps_own_choice_and_plan_approval_when_it_is_on(self):
        self.assertNotIn("per step:", self.w.start().stdout)
        model = {"claude": "opus", "opencode": "acme/big"}[self.RUNTIME]
        level = {"claude": ("--effort", "effort"), "opencode": ("--variant", "variant")}[self.RUNTIME]
        self.w.hw("configure", "--step", "plan", "--model", model, level[0], "high")
        self.w.hw("configure", "--step", "qa", level[0], "low", "--plan-approval", "on")
        said = self.w.start().stdout.splitlines()
        self.assertIn(f"per step: plan model {model}, {level[1]} high · qa {level[1]} low", said)
        self.assertIn("plan approval: on (a build waits for the user's approval of its plan)", said)

    def test_with_nothing_set_start_says_what_applies_on_this_runtime(self):
        (self.w.profile / "config" / f"{self.RUNTIME}.json").write_text("{}")
        said = {"claude": "model and effort are Claude Code's own defaults",
                "opencode": "model NOT SET: `bin/hw coordinator` asks for it, or `bin/hw configure --model <provider/model>`"}
        self.assertIn(f"workers: {said[self.RUNTIME]} · no session limit (change: bin/hw configure)", self.w.start().stdout.splitlines())


per_runtime(globals(), StartSaysWhatWorkersRunOn)


if __name__ == "__main__":
    unittest.main()
