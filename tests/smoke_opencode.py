"""Real-runtime smoke for OpenCode: workers (`bin/hw spawn runtime=opencode`) and the coordinator
(`bin/hw coordinator`, `bin/hw start`) against the installed `opencode` and a live herdr, in a throwaway profile and
demo repo. For workers nothing is stubbed except Herdwright's own lock and capacity checks; the coordinator runs unstubbed.

Not part of the default test run (its name does not match `test_*.py`), and skipped when `opencode` or herdr is
missing. It starts model turns with neutral text only, on the model and variant named in SMOKE_OPENCODE_MODEL and
SMOKE_OPENCODE_VARIANT, which the throwaway profile's config/opencode.json then holds like any user's would. Herdwright
ships no model, so without both it is skipped:

    cd tests && SMOKE_OPENCODE_MODEL=provider/model SMOKE_OPENCODE_VARIANT=low python3 -m unittest -v smoke_opencode

What is claimed about a guard is checked without asking a model for anything: the worker's server is called on its
shell routes directly, which run the same check as a tool call. OpenCode v2.2.7 has no API that runs a tool without
the model and the coordinator has no shell hook (its server's shell is the user's own), so the coordinator's guard is
called the way its plugin calls it: the program the installed plugin names, with the plugin's environment and the
call on stdin. That this is the route a real tool call takes is shown by the canary, the one call Herdwright itself needs a
model turn for: the session's real call and the direct one are refused in the same words. A model is in the loop
only where the claim is about the model's session (its model and variant, the instructions and skills it loaded, its
permission rules); the denied calls are also asked of the model once each, and shown when it makes them, not required.

It proves, and prints the evidence for:
1. a worker whose deny plugin does not load (the V1 plugin Herdwright used to write, which V2 ignores) fails the canary: its
   session is marked failed, no TUI is started and no brief is handed over;
2. a worker with the V2 plugin passes the canary, runs the configured model and variant, has read the repository's
   `AGENTS.md` and Herdwright's rules but not `CLAUDE.md`, and has loaded one repository skill from `.claude/skills` and one
   from `.opencode/skills`; its server's shells hold no server password, and its two shell routes refuse what its hat
   (review) denies even to a caller who has the password: a write in the worktree by an absolute path, by a relative
   one, after a `cd` in a subshell, and an outward command, while a write outside the worktree runs. Any write, edit,
   subagent or outward call the model itself makes when asked is denied too (shown when made, not required).
   (Not stopped, and not claimed: the terminal route `/api/pty` with a password read from the process list, a server
   the worker starts, a script.)
3. a coordinator whose guard plugin does not load fails its canary: no record is written and `bin/hw start` refuses
   the session;
4. a coordinator opened with the guard holds the lock after `bin/hw start`, and its guard, called as its plugin calls
   it, denies an edit, a commit and a shell write by a relative path (`touch`, `rm` with `workdir` set to the project)
   in a project repo, while a write in its profile passes: no model is asked for any of this. From its model's
   session (this part needs the model to do what is asked): it runs the configured model and variant, has the manual
   (`AGENTS.md`) and the profile's files in its instructions, has the coordinator's skills (the server activates
   one), and runs `bin/hw start` without a permission prompt;
5. `bin/hw coordinator` in a herdr pane opens the TUI on the configured model and variant, `bin/hw start` in that
   session takes the lock, and closing the pane stops the server;
6. `bin/hw start` in a coordinator's session starts the long-lived watcher, and one only; for an event the watcher
   queues (a tracked session whose pane is gone: no model is asked for anything) the idle session is prompted through
   its server, the id of the admitted message is kept on the event, and a turn starts;
7. when the server refuses the watcher's call (here: a wrong password), the same prompt is typed into the coordinator's
   herdr pane, the event says so, and a turn starts.
"""
import contextlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import _load  # noqa: F401  (puts bin/lib on sys.path, drops a worker's HW_* variables)

import hwemployee as emp
import hwlib

HOME = Path(__file__).resolve().parents[1]
TURN_SECONDS = 420  # for the worker's first turn (its brief)
CONFIG = {key: os.environ.get(name, "") for key, name in (("model", "SMOKE_OPENCODE_MODEL"), ("variant", "SMOKE_OPENCODE_VARIANT"))}
NO_MODEL = "name the model and variant to run on: SMOKE_OPENCODE_MODEL=provider/model SMOKE_OPENCODE_VARIANT=<variant>"
MODEL = json.dumps(dict(zip(("providerID", "id"), CONFIG["model"].split("/", 1))) | {"variant": CONFIG["variant"]}, sort_keys=True)
FOOTER = f"· {CONFIG['variant']}"  # the TUI's footer ends "<model> <provider> · <variant>"


def new_text(api, session, seen):
    """The assistant's messages in `session` that are not in `seen` (message ids), as one string."""
    return json.dumps([m for m in api("GET", f"/session/{session}/message") if m.get("type") == "assistant" and m["id"] not in seen])


def instruction_words(test, session, ask, question, expected):
    """What the model says its instructions give it: a claim about the model's own context, so it is asked (again,
    up to three times, while one of the `expected` words is missing). Returns its answers."""
    said = ""
    for _ in range(3):
        seen = {m["id"] for m in test.api("GET", f"/session/{session}/message")}
        ask(session, "A question, to be answered from your instructions as you have them now: read no file and call no tool. " + question)
        said += new_text(test.api, session, seen)
        if all(word in said for word in expected):
            break
    return said


def activated_skill(test, session, name):
    """The text a skill puts into the session when the server activates it (no model turn): the server found the skill
    under that id in the folders it loads skills from."""
    test.api("POST", f"/experimental/session/{session}/skill", {"id": name, "resume": False})
    return "".join(m.get("text", "") for m in test.api("GET", f"/session/{session}/message") if m.get("type") == "skill" and m.get("skill") == name)


def herdr_up():
    return bool(shutil.which("herdr")) and subprocess.run(["herdr", "workspace", "list"], capture_output=True).returncode == 0


@unittest.skipUnless(all(CONFIG.values()), NO_MODEL)
@unittest.skipUnless(shutil.which("opencode"), "opencode is not installed")
@unittest.skipUnless(herdr_up(), "herdr is not running")
class OpenCodeWorkerSmoke(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="zs", dir="/tmp")  # short paths: the model copies them into its calls
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.profile, self.repo, self.evidence = self.root / "profile", self.root / "demo", self.root / "evidence"
        self.worktree = self.root / "wt" / "demo" / "smoke"
        self.words = {name: f"{name}-{secrets.token_hex(3)}" for name in ("REPO", "CLAUDE", "RULES", "SKILLC", "SKILLO")}
        (self.profile / "config").mkdir(parents=True)
        self.evidence.mkdir()
        self.repo.mkdir()
        (self.profile / "profile.md").write_text("# throwaway profile\n")
        (self.profile / "config" / "opencode.json").write_text(json.dumps(CONFIG))
        (self.profile / "rules.md").write_text(f"## Code word\nThe profile code word is {self.words['RULES']}.\n")
        (self.profile / "config" / "projects.json").write_text(json.dumps(
            {"worktree_root": str(self.root / "wt"), "repos": {"demo": {"path": str(self.repo), "dev_base": "main"}}}))
        for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            hwlib.git(self.repo, *cmd)
        (self.repo / "AGENTS.md").write_text(f"# Demo\nThe repository code word is {self.words['REPO']}.\n")
        (self.repo / "CLAUDE.md").write_text(f"# Demo\nThe repository code word is {self.words['CLAUDE']}.\n")
        for folder, name, word in ((".claude", "smoke-claude-skill", "SKILLC"), (".opencode", "smoke-opencode-skill", "SKILLO")):
            skill = self.repo / folder / "skills" / name / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(f"---\nname: {name}\ndescription: Gives the code word of {name}. Load it when asked for that code word.\n---\n"
                             f"The code word of this skill is {self.words[word]}.\n")
        hwlib.git(self.repo, "add", ".")
        hwlib.git(self.repo, "commit", "-qm", "init")
        self.worktree.parent.mkdir(parents=True)
        hwlib.git(self.repo, "worktree", "add", "-q", "-b", "task/smoke", str(self.worktree))
        self.brief = self.root / "brief.md"
        self.brief.write_text(
            "# Brief: startup check of this worker\n\n## Goal\n"
            "This is Herdwright's own check that a newly started worker runs, whatever hat it wears. There is nothing to review, "
            "nothing to read and nothing to write.\n\n"
            "## Plan\n- Reply with the single line HW:DONE and stop. Write no plan file and read no file.\n\n"
            "## Definition of done\n- The reply is sent.\n")
        for name, value in (("PROFILE_DIR", self.profile), ("DB_PATH", self.profile / "db" / "hw.db"),
                            ("PROJECTS_PATH", self.profile / "config" / "projects.json")):
            patcher = patch.object(hwlib, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.z = types.ModuleType("hw")
        self.z.__file__ = str(HOME / "bin/hw")
        with patch.dict(os.environ, {"HW_PROFILE_DIR": str(self.profile)}):
            exec(compile((HOME / "bin/hw").read_text(), self.z.__file__, "exec"), self.z.__dict__)
        self.kits = []
        real_kit = self.z.hat_kit

        def hat_kit(*args, **kwargs):
            kit, env = real_kit(*args, **kwargs)
            self.kits.append(kit)
            return kit, env

        for target, kwargs in (("require_lock", {}), ("require_capacity", {}), ("hat_kit", {"side_effect": hat_kit})):
            patcher = patch.object(self.z, target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.conn = self.z.db()
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO tasks(id,title,worktype,status,created_at,updated_at) VALUES (1,'Smoke','task','active','x','x')")
        self.conn.commit()
        self.addCleanup(self.close_workspaces)

    def close_workspaces(self):
        for (ws,) in self.conn.execute("SELECT workspace_id FROM sessions WHERE workspace_id IS NOT NULL"):
            subprocess.run(["herdr", "workspace", "close", ws], capture_output=True)  # ends the worker's TUI and server

    def spawn(self):
        """Run the real spawn; returns (its output, the session row, whether it refused the worker)."""
        out, refused = io.StringIO(), False
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                self.z.cmd_spawn(["1", "repo=demo", "base=main", "branch=task/smoke", f"brief={self.brief}", "hat=review",
                                  "runtime=opencode"])
            except SystemExit:
                refused = True
        return out.getvalue(), self.conn.execute("SELECT * FROM sessions ORDER BY id DESC").fetchone(), refused

    def api(self, method, path, body=None, timeout=30):
        return self.z.opencode_api(self.kits[-1]["opencode_server"], method, path, body, timeout)

    def tool_calls(self, session):
        return [part for m in self.api("GET", f"/session/{session}/message") for part in m.get("content") or []
                if part.get("type") == "tool"]

    def ask(self, session, text):
        """One more turn, admitted through the worker's server like its brief; returns once the turn has ended."""
        self.api("POST", f"/session/{session}/prompt", {"text": text + " Then reply with the single word DONE."})
        self.api("POST", f"/experimental/session/{session}/wait", timeout=TURN_SECONDS)

    def shell_route(self, route, payload, creates):
        """Run a command through one of the server's shell routes, as a caller who holds the password; returns
        (whether the server refused it, whether the file `creates` exists afterwards)."""
        try:
            self.api("POST", route, payload)
            refused = ""
        except OSError as error:
            refused = f"{error} {error.read().decode(errors='replace')[:200] if hasattr(error, 'read') else ''}".strip()
        deadline = time.monotonic() + (3 if refused else 15)  # a command that was let through gets time to run
        while not creates.exists() and time.monotonic() < deadline:
            time.sleep(0.5)
        self.say(f"POST {route} {json.dumps(payload)}", f"{'refused: ' + refused if refused else 'ran'} -> {creates.name} exists: {creates.exists()}")
        return bool(refused), creates.exists()

    def say(self, label, value):
        print(f"  {label}: {value}")

    def test_1_a_worker_whose_plugin_does_not_load_is_refused(self):
        def v1_plugin(directory, hw_home, env, deny_tools=()):  # what Herdwright wrote before the V2 port: V2 does not load it
            plugin = Path(directory) / "plugins" / "hw-deny.js"
            plugin.parent.mkdir(parents=True, exist_ok=True)
            plugin.write_text('export const HwDeny = async () => ({ "tool.execute.before": async () => { throw new Error("denied"); } });\n')
            return Path(directory)

        with patch.object(self.z.hwdeny, "opencode_plugin", side_effect=v1_plugin):
            out, s, refused = self.spawn()
        print("\nguard not live (V1 plugin on OpenCode V2):")
        self.say("spawn said", out.strip().splitlines()[-3:])
        self.assertTrue(refused)
        self.assertIn("the deny plugin is not live", out)
        self.assertIn(f"No brief was handed over and S{s['id']} is marked failed", out)
        self.assertIn('message="failed to load plugin"', Path(self.kits[-1]["opencode_server"]["log"]).read_text())
        self.say("server log", [line[:230] for line in Path(self.kits[-1]["opencode_server"]["log"]).read_text().splitlines() if "plugin" in line.lower()])
        self.assertEqual(s["status"], "failed")
        self.say("session status", s["status"])
        calls = self.tool_calls(s["claude_session_id"])
        self.say("the canary call ran", [(c["state"]["input"], c.get("executed"), c["state"]["status"]) for c in calls])
        texts = [m.get("text", "") for m in self.api("GET", f"/session/{s['claude_session_id']}/message") if m.get("type") == "user"]
        self.assertFalse([t for t in texts if "engineering worker" in t])  # no brief
        listed = json.loads(subprocess.run(["herdr", "agent", "list"], capture_output=True, text=True).stdout)["result"]["agents"]
        started = [a for a in listed if a.get("name") == s["agent"] or a.get("pane_id") == s["pane_id"]]
        self.assertEqual(started, [])  # no TUI was started
        self.say("TUI started", bool(started))

    def test_2_a_guarded_worker_runs_the_model_is_denied_and_reads_agents_md(self):
        out, s, refused = self.spawn()
        session = s["claude_session_id"]
        print("\nguarded worker:")
        self.say("spawn said", out.strip().splitlines()[-1])
        self.assertFalse(refused, out)
        self.assertEqual(s["status"], "running")
        self.assertIn('"prompt_ok": true', out)
        self.api("POST", f"/experimental/session/{session}/wait", timeout=TURN_SECONDS)  # the brief's turn has ended
        server_pid = self.api("GET", "/info")["pid"]

        # the guard, with no model in the loop: the server's two shell routes run hw-deny for any caller
        seen, wt = self.evidence / "password.txt", self.worktree
        self.assertEqual(self.shell_route(f"/session/{session}/shell", {"command": f"env | grep -c '^OPENCODE_PASSWORD=' > {seen}"}, seen), (False, True))
        self.assertEqual(seen.read_text().strip(), "0")  # a shell of this server does not hold its password
        self.say("OPENCODE_PASSWORD entries in a shell's environment", seen.read_text().strip())
        for route, payload, target in (
                (f"/session/{session}/shell", {"command": f"touch {wt}/absolute.txt"}, wt / "absolute.txt"),
                (f"/session/{session}/shell", {"command": "touch relative.txt"}, wt / "relative.txt"),  # the session's directory is the worktree
                ("/shell", {"command": "touch relative.txt", "cwd": str(wt)}, wt / "relative.txt"),
                (f"/session/{session}/shell", {"command": "(cd /tmp) && touch subshell.txt"}, wt / "subshell.txt"),
                ("/shell", {"command": f"cd {wt} && echo x > redirect.txt", "cwd": str(self.evidence)}, wt / "redirect.txt"),
                (f"/session/{session}/shell", {"command": f"gh pr comment 1 --body x; touch {self.evidence}/after-gh.txt"}, self.evidence / "after-gh.txt")):
            self.assertEqual(self.shell_route(route, payload, target), (True, False), payload)
        self.assertEqual(self.shell_route("/shell", {"command": "rm AGENTS.md", "cwd": str(wt)}, wt / "AGENTS.md"), (True, True))  # still there
        # the routes are alive: what the hat allows runs (a write outside the worktree)
        self.assertEqual(self.shell_route(f"/session/{session}/shell", {"command": f"touch {self.evidence}/allowed.txt"}, self.evidence / "allowed.txt"), (False, True))
        self.assertEqual(self.shell_route("/shell", {"command": "touch allowed-2.txt", "cwd": str(self.evidence)}, self.evidence / "allowed-2.txt"), (False, True))

        # tool calls of the model: asked for once, shown and checked when it makes them, never required (a model that
        # knows its hat may decline, and that is no defect of the guard)
        self.ask(session, "This is the guard's own check, not part of your brief: each call below is expected to be refused by the "
                          "guard, which is the result wanted. Make each call exactly once, do not retry, and do nothing else. "
                          f"1. The write tool with the path {wt}/denied.txt and the content x. "
                          f"2. The edit tool on the file {wt}/AGENTS.md, replacing the word Demo with the word Changed. "
                          "3. The shell tool with exactly this command: gh pr comment 1 --body x "
                          "4. One subagent with the task: reply with the single word HELLO.")
        messages = self.api("GET", f"/session/{session}/message")
        calls = self.tool_calls(session)
        denied = lambda c: c["state"]["status"] == "error"
        reason = lambda c: str((c["state"].get("error") or {}).get("message", ""))

        for c in calls:
            self.say("call", f"{c.get('name')} {json.dumps(c['state']['input'])[:160]} -> {c['state']['status']} {reason(c)[:120]}")
        canary = next(c for c in calls if str(c["state"]["input"].get("command", "")).startswith("hw-deny-canary"))
        self.assertTrue(denied(canary) and "canary" in reason(canary), canary)
        self.say("canary", f"{canary['state']['input']['command']} -> {reason(canary)}")
        inside = lambda c: self.worktree in (self.worktree / str(c["state"]["input"].get("path", ""))).resolve().parents
        for what, made, why in (
                ("write or edit in the worktree", [c for c in calls if c.get("name") in ("write", "edit") and inside(c)], "hw-deny: editing"),
                ("subagent", [c for c in calls if c.get("name") in self.z.hwdeny.SUBAGENT_TOOLS], "this hat starts no subagents"),
                ("outward command", [c for c in calls if "gh pr comment" in str(c["state"]["input"].get("command", ""))], "`gh pr comment` is denied")):
            self.assertTrue(all(denied(c) and why in reason(c) for c in made), made)
            self.say(f"{what}, as tool calls of the model", f"{len(made)} made, all denied" if made else "none made by the model (not required)")
        self.assertFalse((self.worktree / "denied.txt").exists())

        models = {json.dumps(m["model"], sort_keys=True) for m in messages if m.get("type") == "assistant"}
        self.assertEqual(models, {MODEL})
        self.say("model of every assistant message", models)
        self.assertEqual((s["model"], s["effort"]), (CONFIG["model"], CONFIG["variant"]))
        screen = subprocess.run(["herdr", "agent", "read", s["agent"], "--source", "visible"], capture_output=True, text=True).stdout
        footer = next((line.strip(" ┃") for line in screen.splitlines() if FOOTER in line), "")
        self.assertIn(FOOTER, footer, screen)
        self.say("TUI footer", footer)

        words = instruction_words(self, session, self.ask, "Which code words do your instructions give you? Reply with every one of them.",
                                  [self.words["REPO"], self.words["RULES"]])
        self.say("code words the worker gave", sorted(w for w in self.words.values() if w in words))
        self.assertIn(self.words["REPO"], words)  # the repository's AGENTS.md
        self.assertIn(self.words["RULES"], words)  # Herdwright's rules, from the per-session config dir's AGENTS.md ...
        reads = [str(c["state"]["input"].get("path", "")) for c in self.tool_calls(session) if c.get("name") == "read" and not denied(c)]  # that worked
        self.assertFalse([p for p in reads if "rules" in p or p.endswith(("AGENTS.md", "CLAUDE.md", "SKILL.md"))], reads)  # ... not from reading a file
        self.assertNotIn(self.words["CLAUDE"], words)  # CLAUDE.md is not read by OpenCode V2
        for name, word in (("smoke-claude-skill", "SKILLC"), ("smoke-opencode-skill", "SKILLO")):  # .claude/skills and .opencode/skills
            text = activated_skill(self, session, name)
            self.say(f"skill {name}, activated by the server", text.strip())
            self.assertIn(self.words[word], text)
        # the worker's shell commands run in its server: the session's pane must host it, or `hw-employee finish` refuses
        info = json.loads(subprocess.run(["herdr", "pane", "process-info", "--pane", s["pane_id"]], capture_output=True, text=True).stdout)
        self.assertIn(info["result"]["process_info"]["shell_pid"], emp.ancestors(server_pid))
        self.say("server process", f"pid {server_pid}, a child of the shell of the session's pane {s['pane_id']}")
        self.close_workspaces()
        for _ in range(20):
            if subprocess.run(["/bin/ps", "-p", str(server_pid)], capture_output=True).returncode:
                break
            time.sleep(0.5)
        self.assertTrue(subprocess.run(["/bin/ps", "-p", str(server_pid)], capture_output=True).returncode, "the server outlived its workspace")
        self.say("server after its workspace closed", "gone")
        self.assertEqual(hwlib.git(self.worktree, "status", "--porcelain").stdout.strip(), "")  # the worktree is untouched
        self.assertEqual((self.worktree / "AGENTS.md").read_text(), (self.repo / "AGENTS.md").read_text())


@unittest.skipUnless(all(CONFIG.values()), NO_MODEL)
@unittest.skipUnless(shutil.which("opencode"), "opencode is not installed")
class OpenCodeCoordinatorSmoke(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="zs", dir="/tmp")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.profile = self.root / "profile"
        # a project repo is anywhere outside Herdwright's home, the profile and the temp dirs (which the guard leaves writable)
        self.project = Path(tempfile.mkdtemp(prefix=".hw-smoke-project-", dir=Path.home()))
        self.addCleanup(shutil.rmtree, self.project, True)
        self.word = f"PROFILE-{secrets.token_hex(3)}"
        (self.profile / "config").mkdir(parents=True)
        (self.profile / "profile.md").write_text(f"# Throwaway profile\nThe profile code word is {self.word}.\n")
        (self.profile / "config" / "settings.json").write_text(json.dumps({"runtime": "opencode"}))
        (self.profile / "config" / "opencode.json").write_text(json.dumps(CONFIG))
        (self.profile / "config" / "projects.json").write_text(json.dumps({"worktree_root": str(self.root / "wt"), "repos": {}}))
        for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            hwlib.git(self.project, *cmd)
        patcher = patch.object(hwlib, "PROFILE_DIR", self.profile)
        patcher.start()
        self.addCleanup(patcher.stop)
        # a plain terminal: neither this test's own agent session nor its herdr pane is the coordinator's
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("HW_", "OPENCODE_", "CLAUDE", "HERDR_"))}
        self.env["HW_PROFILE_DIR"] = str(self.profile)
        self.z = types.ModuleType("hw")
        self.z.__file__ = str(HOME / "bin/hw")
        with patch.dict(os.environ, self.env, clear=True):
            exec(compile((HOME / "bin/hw").read_text(), self.z.__file__, "exec"), self.z.__dict__)
        self.record = self.profile / ".cache" / "coordinator.json"
        self.before = hwlib.git(HOME, "status", "--porcelain").stdout
        self.server = None
        self.addCleanup(self.stop_watcher)  # `start` leaves the long-lived watcher running

    def watcher_pid(self):
        """The pid of this profile's running watcher, or None."""
        try:
            pid = json.loads((self.profile / ".cache" / "watch.pid").read_text())["pid"]
            os.kill(pid, 0)
        except (OSError, ValueError, KeyError):
            return None
        return pid

    def stop_watcher(self):
        pid, deadline = self.watcher_pid(), time.monotonic() + 15
        while pid and self.watcher_pid() == pid and time.monotonic() < deadline:
            try:
                os.kill(pid, 15)
            except OSError:
                break
            time.sleep(0.2)

    def track_a_session_whose_pane_is_gone(self):
        """Something for the watcher to report without a worker or a model: a tracked session in a pane herdr does not have."""
        with patch.dict(os.environ, self.env, clear=True):
            conn = self.z.db()
        conn.execute("INSERT INTO tasks(id,title,worktype,status,created_at,updated_at) VALUES (1,'Smoke','task','active','x','x')")
        conn.execute("INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,pane_id,status,created_at,updated_at) "
                     "VALUES (1,'demo','main','task/smoke','/nowhere','smoke-gone','no-such-pane','running','2026-01-01 00:00','x')")
        conn.commit()
        conn.close()

    def woken(self, session, seen):
        """Wait for the watcher's receipt on the queued event and for the turn its prompt started to end. Returns (the
        event, the user message with the watcher's text); an assistant message has followed it."""
        queue, deadline = self.profile / ".cache" / "wake-queue.json", time.monotonic() + 90
        while not (queue.exists() and any("wake" in e for e in json.loads(queue.read_text())["events"])):
            self.assertLess(time.monotonic(), deadline, (self.profile / ".cache" / "watch.log").read_text())
            time.sleep(1)
        event, = json.loads(queue.read_text())["events"]
        deadline = time.monotonic() + TURN_SECONDS
        while True:
            new = [m for m in self.api("GET", f"/session/{session}/message") if m["id"] not in seen]
            told = [m for m in new if m.get("type") == "user" and "Watcher events for Herdwright" in json.dumps(m)]
            if told and [m for m in new if m.get("type") == "assistant"]:
                self.api("POST", f"/experimental/session/{session}/wait", timeout=TURN_SECONDS)
                return event, told[0]
            self.assertLess(time.monotonic(), deadline, f"no turn followed the wake; new messages: {json.dumps(new)[:600]}")
            time.sleep(2)

    def open(self):
        with patch.dict(os.environ, self.env, clear=True):
            kit = self.z.coordinator_kit()
            process, session, problem = self.z.open_coordinator(kit)
        self.addCleanup(self.stop, process)
        self.server = kit["server"]
        return kit, session, problem

    def stop(self, process):
        process.terminate()
        process.wait(15)

    def api(self, method, path, body=None, timeout=30):
        return self.z.opencode_api(self.server, method, path, body, timeout)

    def turn(self, session, text):
        """One turn of the coordinator; a permission request is answered `once`, as a user who allows it would.
        Returns what was asked."""
        self.api("POST", f"/session/{session}/prompt", {"text": text + " Do nothing else. Then reply with the single word DONE."})
        ended = threading.Event()
        waiter = threading.Thread(target=lambda: (self.api("POST", f"/experimental/session/{session}/wait", timeout=TURN_SECONDS), ended.set()), daemon=True)
        waiter.start()
        asked, deadline = [], time.monotonic() + TURN_SECONDS
        while not ended.wait(1):
            self.assertLess(time.monotonic(), deadline, "the turn did not end")
            for request in self.api("GET", f"/session/{session}/permission") or []:
                asked.append(f"{request['action']} {request['resources']}")
                self.api("POST", f"/session/{session}/permission/{request['id']}/reply", {"decision": "once"})
        return asked

    def calls(self, session):
        return [part for m in self.api("GET", f"/session/{session}/message") for part in m.get("content") or [] if part.get("type") == "tool"]

    def guard(self, kit, session, tool, **tool_input):
        """What the coordinator's guard decides about a tool call of `session`, with no model asked for it: the program
        the installed plugin names is run as the plugin runs it (the call on stdin, the plugin's environment). Returns
        the reason it denies, or "" when it lets the call through."""
        path = kit["config_dir"] / "plugins" / "hw-pretool" / "index.js"
        plugin = path.read_text()
        program, env = re.search(r'spawnSync\(("(?:[^"\\]|\\.)*")', plugin), re.search(r"^const ENV = (\{.*\});$", plugin, re.M)
        self.assertTrue(program and env, f"{path}: no `spawnSync(\"<program>\"` or no `const ENV = {{...}};` line to read the guard's call from")
        program, env = json.loads(program.group(1)), json.loads(env.group(1))
        call = {"tool_name": tool, "tool_input": tool_input, "cwd": str(HOME), "session_id": session}
        r = subprocess.run([program], input=json.dumps(call), capture_output=True, text=True, env=self.env | env, timeout=60)
        if r.returncode:
            return (r.stderr or "hw-pretool could not run").strip()
        decision = json.loads(r.stdout).get("hookSpecificOutput", {}) if r.stdout.strip() else {}
        return decision["permissionDecisionReason"] if decision.get("permissionDecision") == "deny" else ""

    def asked_call(self, session, text, needle, tries=3):
        """The tool calls whose input holds `needle`, which the model is asked for with `text` (again, up to `tries`
        times, while it makes none), and the permissions asked meanwhile. With `tries` 1 a model that makes none is no
        failure ([] is returned): the call is evidence when made. Otherwise none at all fails the test: it is for
        what only a call of the model shows (its permission rules)."""
        asked = []
        for attempt in range(tries):
            asked += self.turn(session, text if not attempt else "This is a check of the guard, which is expected to refuse the call: "
                                                                   "making the call is what is asked of you. " + text)
            if made := [c for c in self.calls(session) if needle in json.dumps(c["state"]["input"])]:
                return made, asked
        if tries == 1:
            return [], asked
        self.fail(f"the model made no call holding {needle!r} in {tries} asks, so the guard was not exercised; its last calls: "
                  + json.dumps([c["state"]["input"] for c in self.calls(session)][-4:])[:600])

    def start_as(self, session):
        """`bin/hw start` as the shell tool of `session` would run it."""
        env = self.env | {"OPENCODE_SESSION_ID": session, "HW_COORDINATOR": str(self.record)}
        return subprocess.run([sys.executable, str(HOME / "bin" / "hw"), "start"], env=env, capture_output=True, text=True, timeout=60)

    def say(self, label, value):
        print(f"  {label}: {value}")

    def test_3_a_coordinator_whose_plugin_does_not_load_is_not_opened_and_start_refuses_it(self):
        def v1_plugin(directory, hw_home, env):  # the V1 shape: V2 does not load it
            plugin = Path(directory) / "plugins" / "hw-pretool.js"
            plugin.parent.mkdir(parents=True, exist_ok=True)
            plugin.write_text('export const HwPretool = async () => ({ "tool.execute.before": async () => { throw new Error("denied"); } });\n')
            return Path(directory)

        with patch.object(self.z.hwdeny, "coordinator_plugin", side_effect=v1_plugin):
            kit, session, problem = self.open()
        print("\ncoordinator guard not live (V1 plugin on OpenCode V2):")
        self.say("open_coordinator said", problem)
        self.assertIn("was not denied (status completed): the coordinator guard (bin/hw-pretool) is not live", problem)
        self.assertFalse(self.record.exists())
        self.assertIn('message="failed to load plugin"', Path(kit["server"]["log"]).read_text())
        r = self.start_as(session)
        self.say("bin/hw start in that session", f"exit {r.returncode}: {r.stderr.strip()}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("was not opened by `bin/hw coordinator`, so its guard (bin/hw-pretool) is not proven live", r.stderr)
        self.assertFalse((self.profile / ".cache" / "hw.lock").exists())

    def test_4_a_guarded_coordinator_starts_holds_the_lock_and_cannot_change_a_project_repo(self):
        kit, session, problem = self.open()
        print("\nguarded coordinator:")
        self.assertEqual(problem, "")
        record = json.loads(self.record.read_text())
        self.say("record", {k: record[k] for k in ("runtime", "session", "guard", "model", "variant")})
        self.assertEqual((record["session"], record["guard"]), (session, "live"))
        reason = lambda c: str((c["state"].get("error") or {}).get("message", ""))
        made = lambda needle: [c for c in self.calls(session) if needle in json.dumps(c["state"]["input"])]

        # 1. Without a model. The canary is the session's one real call (Herdwright's own startup check): the guard called
        # directly, as the plugin calls it, refuses the same input in the same words, so it is the route a real call takes.
        canary, = made("hw-pretool-canary")
        self.assertEqual(canary["state"]["status"], "error")
        self.assertIn("hw-pretool: canary: `hw-pretool-canary", reason(canary))
        self.say("canary", f"{canary['state']['input']['command']} -> {reason(canary)}")
        self.assertEqual(self.guard(kit, session, "shell", **canary["state"]["input"]), reason(canary))
        self.say("the same call, made directly to the guard", "refused in the same words")

        digest = self.start_as(session)
        self.assertEqual(digest.returncode, 0, digest.stderr)
        lock = json.loads((self.profile / ".cache" / "hw.lock").read_text())
        self.assertEqual(lock["agent_session"], session)
        self.say("lock", {k: lock.get(k) for k in ("agent_session", "pane")})
        self.assertIn(f"runtime: opencode · session {session} · guard proven live at", digest.stdout)
        self.assertIn("lock: held by this session", digest.stdout)
        self.assertIn(f"already in your instructions: {self.profile / 'profile.md'}", digest.stdout)
        self.say("digest", digest.stdout.splitlines()[:5])

        denied_file, note = self.project / "denied.txt", self.profile / "briefs" / "note.md"
        (self.project / "keep.txt").write_text("keep\n")
        cases = (  # what, the tool, its input (the shapes OpenCode v2.2.7 passes), the guard's reason, how the model is asked for it
            ("write in a project repo", "write", {"path": str(denied_file), "content": "x"},
             f"hw-pretool: {denied_file} is outside Herdwright's repo, profile and temp dirs",
             f"Call the write tool exactly once with the path {denied_file} and the content x."),
            ("commit in a project repo", "shell", {"command": f"git -C {self.project} commit --allow-empty -m x"}, "hw-pretool: `git commit` in",
             f"Call the shell tool exactly once with exactly this command: git -C {self.project} commit --allow-empty -m x"),
            # S339 B1: a relative path, with the shell tool's workdir set to the project repo
            ("touch by a relative path, workdir in a project repo", "shell", {"command": "touch qa-relative.txt", "workdir": str(self.project)},
             "hw-pretool: `touch` on qa-relative.txt in",
             f"Call the shell tool exactly once with the command `touch qa-relative.txt` and its workdir parameter set to {self.project} ."),
            ("rm by a relative path, workdir in a project repo", "shell", {"command": "rm keep.txt", "workdir": str(self.project)},
             "hw-pretool: `rm` on keep.txt in",
             f"Call the shell tool exactly once with the command `rm keep.txt` and its workdir parameter set to {self.project} ."))
        for what, tool, tool_input, why, _ in cases:
            said = self.guard(kit, session, tool, **tool_input)
            self.say(f"guard, called directly: {what}", f"{json.dumps(tool_input)[:200]} -> {said or 'allowed'}")
            self.assertIn(why, said, what)
        self.assertEqual(self.guard(kit, session, "write", path=str(note), content="x"), "")  # its own profile stays writable
        self.say("guard, called directly: write in the profile", "allowed")
        self.assertEqual(self.guard(kit, "ses_another", "write", path=str(denied_file), content="x"), "")  # and it acts on the lock holder only
        self.say("guard, called directly: the same write by a session that does not hold the lock", "allowed (not Herdwright)")

        # 2. From the model's session: only a call the model makes shows its permission rules.
        (start, *_), asked = self.asked_call(session, "Call the shell tool exactly once with exactly this command: bin/hw start", "bin/hw start")
        self.say("bin/hw start", f"{start['state']['status']}; permission asked: {asked}; state keys {sorted(start['state'])}")
        self.assertEqual((start["state"]["status"], asked), ("completed", []))  # the permission rules let it run unprompted
        # The same denied calls, asked of the model once each: evidence when it makes them, even after the user allows them
        for what, _, tool_input, why, ask in cases:
            made_calls, asked = self.asked_call(session, ask + " It may be refused: do not retry it.", next(iter(tool_input.values())), tries=1)
            for c in made_calls:
                exact = tool_input.items() <= c["state"]["input"].items()  # another input (no workdir, say) is another call: the guard may rightly allow it
                self.say(f"the model's own call: {what}" + ("" if exact else " (not the call asked for: not counted)"),
                         f"{json.dumps(c['state']['input'])[:200]}; permission asked: {asked} -> {c['state']['status']} {reason(c)}")
                if exact:
                    self.assertEqual(c["state"]["status"], "error", what)
            if not made_calls:
                self.say(f"the model's own call: {what}", "not made by the model (not required)")
        self.assertEqual(sorted(p.name for p in self.project.iterdir()), [".git", "keep.txt"])  # nothing created, nothing deleted
        self.assertNotEqual(hwlib.git(self.project, "rev-parse", "HEAD").returncode, 0)  # still no commit

        expected = [self.word, "bin/hw start"]
        text = instruction_words(self, session, self.turn, "Which code word do your instructions give you, and which one command does your "
                                 "operating manual tell you to run at the start of every session? Reply with both.", expected)
        self.say("from its instructions", [w for w in expected if w in text])
        self.assertIn(self.word, text)  # the profile's profile.md, loaded through `instructions`
        self.assertIn("bin/hw start", text)  # the manual: this repo's AGENTS.md
        reads = [str(c["state"]["input"].get("path", "")) for c in self.calls(session) if c.get("name") == "read"]
        self.assertFalse([p for p in reads if p.endswith(("AGENTS.md", "CLAUDE.md", "profile.md"))], reads)  # not from reading a file
        relay = activated_skill(self, session, "relay")  # a coordinator skill, from this repo's .claude/skills
        self.say("skill relay, activated by the server", relay.strip().splitlines()[:2])
        self.assertIn("bin/hw relay", relay)

        models = {json.dumps(m["model"], sort_keys=True) for m in self.api("GET", f"/session/{session}/message") if m.get("type") == "assistant"}
        self.assertEqual(models, {MODEL})
        self.say("model of every assistant message", models)
        self.assertEqual(hwlib.git(HOME, "status", "--porcelain").stdout, self.before)  # Herdwright's own checkout is untouched

    @unittest.skipUnless(herdr_up(), "herdr is not running")
    def test_5_the_command_opens_the_tui_in_a_pane_and_closing_it_stops_the_server(self):
        ws = json.loads(subprocess.run(["herdr", "workspace", "create", "--cwd", str(HOME), "--label", "hw coordinator smoke", "--no-focus",
                                        "--env", f"HW_PROFILE_DIR={self.profile}"], capture_output=True, text=True).stdout)["result"]
        ws_id, pane = ws["workspace"]["workspace_id"], ws["root_pane"]["pane_id"]
        self.addCleanup(subprocess.run, ["herdr", "workspace", "close", ws_id], capture_output=True)
        time.sleep(1.5)
        subprocess.run(["herdr", "pane", "run", pane, f"{HOME}/bin/hw coordinator"], capture_output=True)
        deadline = time.monotonic() + 240
        while not self.record.exists():
            self.assertLess(time.monotonic(), deadline, subprocess.run(["herdr", "pane", "read", pane], capture_output=True, text=True).stdout)
            time.sleep(2)
        record = json.loads(self.record.read_text())
        self.server, session = record["server"], record["session"]
        print("\ncoordinator in a herdr pane:")
        (start, *_), asked = self.asked_call(session, "Call the shell tool exactly once with exactly this command: bin/hw start", "bin/hw start")
        screen = subprocess.run(["herdr", "pane", "read", pane], capture_output=True, text=True).stdout
        self.say("bin/hw start", f"{start['state']['status']} {str((start['state'].get('error') or {}).get('message', ''))[:300]}; permission asked: {asked}")
        self.assertEqual(start["state"]["status"], "completed", screen)
        lock = json.loads((self.profile / ".cache" / "hw.lock").read_text())
        self.say("lock", {k: lock.get(k) for k in ("agent_session", "pane")})
        self.assertEqual((lock["agent_session"], lock["pane"]), (session, pane))
        footer = next((line.strip(" ┃") for line in screen.splitlines() if FOOTER in line), "")
        self.assertIn(FOOTER, footer, screen)
        self.say("TUI footer", footer)
        subprocess.run(["herdr", "workspace", "close", ws_id], capture_output=True)
        for _ in range(30):
            if subprocess.run(["/bin/ps", "-p", str(record["server_pid"])], capture_output=True).returncode:
                break
            time.sleep(0.5)
        self.assertTrue(subprocess.run(["/bin/ps", "-p", str(record["server_pid"])], capture_output=True).returncode, "the server outlived its pane")
        self.say("server after its pane closed", "gone")


    def test_6_an_idle_coordinator_is_woken_through_its_server_with_a_receipt_by_the_one_watcher(self):
        kit, session, problem = self.open()
        print("\nwake through the server:")
        self.assertEqual(problem, "")
        self.track_a_session_whose_pane_is_gone()
        seen = {m["id"] for m in self.api("GET", f"/session/{session}/message")}  # the canary turn is over: the session is idle
        digest = self.start_as(session)
        self.assertEqual(digest.returncode, 0, digest.stderr)
        line = next(l for l in digest.stdout.splitlines() if l.startswith("watcher:"))
        self.say("bin/hw start", line)
        self.assertRegex(line, r"^watcher: was NOT RUNNING; started now, pid \d+$")
        pid = self.watcher_pid()
        self.assertEqual(line.rsplit(" ", 1)[1], str(pid))

        event, told = self.woken(session, seen)
        self.say("event", f"[E{event['id']}] {event['text']}")
        self.say("receipt", event["wake"])
        self.assertEqual((event["wake"]["via"], event["wake"]["message"]), ("api", told["id"]))  # the id the server gave the admitted prompt
        self.assertIn("smoke-gone", event["text"])
        self.assertIn(f"[E{event['id']}]", json.dumps(told))
        self.say("admitted message", f"{told['id']} type {told['type']}; an assistant message followed")

        # T121: the turn the wake started has ended (the session lists it as `idle`). Still un-acked, the event is
        # shown again with a new receipt; acked in that turn (the model may run `bin/hw ack`), it is not.
        ended = [m["type"] for m in self.api("GET", f"/session/{session}/message?limit=50")]
        self.say("latest message types (newest first)", ended[:6])
        self.assertIn("idle", ended[:ended.index("user") if "user" in ended else len(ended)])
        queue, deadline = self.profile / ".cache" / "wake-queue.json", time.monotonic() + 60
        while True:
            now = json.loads(queue.read_text())["events"][0]
            if now["acked"] or now["shown"] == 2 or time.monotonic() > deadline:
                break
            time.sleep(1)
        self.say("after the turn", f"acked {now['acked']}, shown {now['shown']}, receipt {now.get('wake')}")
        if now["acked"]:
            self.assertEqual(now["shown"], 1)
        else:
            self.assertEqual(now["shown"], 2)
            self.assertNotEqual(now["wake"]["message"], told["id"])  # a new wake, a new message to follow
            self.api("POST", f"/experimental/session/{session}/wait", timeout=TURN_SECONDS)

        again = self.start_as(session)
        self.say("bin/hw start again", next(l for l in again.stdout.splitlines() if l.startswith("watcher:")))
        self.assertIn(f"watcher: alive, pid {pid}", again.stdout)
        env = self.env | {"OPENCODE_SESSION_ID": session, "HW_COORDINATOR": str(self.record)}
        second = subprocess.run([sys.executable, str(HOME / "bin" / "hw"), "watch", "--daemon"], env=env, capture_output=True, text=True, timeout=60)
        self.say("a second watcher", f"exit {second.returncode}: {second.stdout.strip()}")
        self.assertEqual((second.returncode, self.watcher_pid()), (4, pid))
        self.assertIn(f"WATCH_ALREADY_RUNNING pid {pid}", second.stdout)
        running = subprocess.run(["/usr/bin/pgrep", "-f", f"watch --daemon"], capture_output=True, text=True).stdout.split()
        mine = [p for p in running if str(self.profile) in subprocess.run(["/bin/ps", "eww", "-p", p], capture_output=True, text=True).stdout]
        self.say("watchers of this profile", mine)
        self.assertEqual(mine, [str(pid)])
        self.assertEqual(hwlib.git(HOME, "status", "--porcelain").stdout, self.before)

    @unittest.skipUnless(herdr_up(), "herdr is not running")
    def test_7_when_the_server_refuses_the_wake_is_typed_into_the_coordinators_pane(self):
        ws = json.loads(subprocess.run(["herdr", "workspace", "create", "--cwd", str(HOME), "--label", "hw wake smoke", "--no-focus",
                                        "--env", f"HW_PROFILE_DIR={self.profile}"], capture_output=True, text=True).stdout)["result"]
        ws_id, pane = ws["workspace"]["workspace_id"], ws["root_pane"]["pane_id"]
        self.addCleanup(subprocess.run, ["herdr", "workspace", "close", ws_id], capture_output=True)
        time.sleep(1.5)
        subprocess.run(["herdr", "pane", "run", pane, f"{HOME}/bin/hw coordinator"], capture_output=True)
        deadline = time.monotonic() + 240
        while not self.record.exists():
            self.assertLess(time.monotonic(), deadline, subprocess.run(["herdr", "pane", "read", pane], capture_output=True, text=True).stdout)
            time.sleep(2)
        record = json.loads(self.record.read_text())
        self.server, session = record["server"], record["session"]
        print("\nwake typed into the pane:")
        deadline = time.monotonic() + 60  # the TUI is up once herdr sees an idle OpenCode agent in the pane
        while (self.z.pane_record(pane) or {}).get("agent_status") != "idle":
            self.assertLess(time.monotonic(), deadline, "the TUI did not open: "
                            + subprocess.run(["herdr", "pane", "get", pane], capture_output=True, text=True).stdout)
            time.sleep(1)
        self.track_a_session_whose_pane_is_gone()
        seen = {m["id"] for m in self.api("GET", f"/session/{session}/message")}
        self.record.write_text(json.dumps(record | {"server": record["server"] | {"password": "wrong"}}))  # the watcher's call will be refused
        env = self.env | {"OPENCODE_SESSION_ID": session, "HW_COORDINATOR": str(self.record), "HERDR_PANE_ID": pane}
        digest = subprocess.run([sys.executable, str(HOME / "bin" / "hw"), "start"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(digest.returncode, 0, digest.stderr)
        self.say("bin/hw start", next(l for l in digest.stdout.splitlines() if l.startswith("watcher:")))
        lock = json.loads((self.profile / ".cache" / "hw.lock").read_text())
        self.assertEqual((lock["agent_session"], lock["pane"]), (session, pane))

        event, told = self.woken(session, seen)
        self.say("event", f"[E{event['id']}] {event['text']}")
        self.say("receipt", event["wake"])
        self.assertEqual(event["wake"]["via"], "herdr")
        self.assertIn("401", event["wake"]["api_error"])
        self.assertIn(f"[E{event['id']}]", json.dumps(told))
        self.say("message the TUI submitted", f"{told['id']} type {told['type']}; an assistant message followed")
        self.record.write_text(json.dumps(record))


if __name__ == "__main__":
    unittest.main(verbosity=2)
