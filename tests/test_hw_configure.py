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

    def test_with_nothing_set_start_says_what_applies_on_this_runtime(self):
        (self.w.profile / "config" / f"{self.RUNTIME}.json").write_text("{}")
        said = {"claude": "model and effort are Claude Code's own defaults",
                "opencode": "model NOT SET: `bin/hw coordinator` asks for it, or `bin/hw configure --model <provider/model>`"}
        self.assertIn(f"workers: {said[self.RUNTIME]} · no session limit (change: bin/hw configure)", self.w.start().stdout.splitlines())


per_runtime(globals(), StartSaysWhatWorkersRunOn)


if __name__ == "__main__":
    unittest.main()
