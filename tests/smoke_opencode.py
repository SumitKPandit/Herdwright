"""Real-runtime smoke for OpenCode workers: `bin/zed spawn runtime=opencode` against the installed `opencode` and a live
herdr, in a throwaway profile and demo repo. Nothing is stubbed except Zed's own lock and capacity checks.

Not part of the default test run (its name does not match `test_*.py`), and skipped when `opencode` or herdr is
missing. It starts model turns on the configured model (config/opencode.json's defaults), with neutral text only:

    cd tests && python3 -m unittest -v smoke_opencode

It proves, and prints the evidence for:
1. a worker whose deny plugin does not load (the V1 plugin Zed used to write, which V2 ignores) fails the canary: its
   session is marked failed, no TUI is started and no brief is handed over;
2. a worker with the V2 plugin passes the canary, runs the configured model and variant, is denied what its hat (review)
   denies, and has read the repository's `AGENTS.md` and Zed's rules, but not `CLAUDE.md`.
"""
import contextlib
import io
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import _load  # noqa: F401  (puts bin/lib on sys.path, drops a worker's ZED_* variables)

import zedemployee as emp
import zedlib

HOME = Path(__file__).resolve().parents[1]
TURN_SECONDS = 420  # for the worker's first turn (its brief)


def herdr_up():
    return bool(shutil.which("herdr")) and subprocess.run(["herdr", "workspace", "list"], capture_output=True).returncode == 0


@unittest.skipUnless(shutil.which("opencode"), "opencode is not installed")
@unittest.skipUnless(herdr_up(), "herdr is not running")
class OpenCodeWorkerSmoke(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="zs", dir="/tmp")  # short paths: the model copies them into its calls
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.profile, self.repo, self.evidence = self.root / "profile", self.root / "demo", self.root / "evidence"
        self.worktree = self.root / "wt" / "demo" / "smoke"
        self.words = {name: f"{name}-{secrets.token_hex(3)}" for name in ("REPO", "CLAUDE", "RULES")}
        (self.profile / "config").mkdir(parents=True)
        self.evidence.mkdir()
        self.repo.mkdir()
        (self.profile / "profile.md").write_text("# throwaway profile\n")
        (self.profile / "rules.md").write_text(f"## Code word\nThe profile code word is {self.words['RULES']}.\n")
        (self.profile / "config" / "projects.json").write_text(json.dumps(
            {"worktree_root": str(self.root / "wt"), "repos": {"demo": {"path": str(self.repo), "dev_base": "main"}}}))
        for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            zedlib.git(self.repo, *cmd)
        (self.repo / "AGENTS.md").write_text(f"# Demo\nThe repository code word is {self.words['REPO']}.\n")
        (self.repo / "CLAUDE.md").write_text(f"# Demo\nThe repository code word is {self.words['CLAUDE']}.\n")
        zedlib.git(self.repo, "add", ".")
        zedlib.git(self.repo, "commit", "-qm", "init")
        self.worktree.parent.mkdir(parents=True)
        zedlib.git(self.repo, "worktree", "add", "-q", "-b", "task/smoke", str(self.worktree))
        self.brief = self.root / "brief.md"
        self.brief.write_text(
            "# Brief: startup check of this worker\n\n## Goal\n"
            f"1. Write every code word that your instructions give you (not this brief) into the file "
            f"{self.evidence}/words.txt, one per line.\n"
            "2. Reply with the single line ZED:DONE and stop.\n\n"
            "## Plan\n- The two steps above are the plan. Write no plan file and read no other file.\n\n"
            "## Definition of done\n- Step 2 is reached.\n")
        for name, value in (("PROFILE_DIR", self.profile), ("DB_PATH", self.profile / "db" / "zed.db"),
                            ("PROJECTS_PATH", self.profile / "config" / "projects.json")):
            patcher = patch.object(zedlib, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.z = types.ModuleType("zed")
        self.z.__file__ = str(HOME / "bin/zed")
        with patch.dict(os.environ, {"ZED_PROFILE_DIR": str(self.profile)}):
            exec(compile((HOME / "bin/zed").read_text(), self.z.__file__, "exec"), self.z.__dict__)
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
        self.api("POST", f"/session/{session}/prompt", {"text": text + " It is expected to be refused: do not retry it "
                                                                "and do nothing else. Then reply with the single word DONE."})
        self.api("POST", f"/experimental/session/{session}/wait", timeout=TURN_SECONDS)

    def say(self, label, value):
        print(f"  {label}: {value}")

    def test_1_a_worker_whose_plugin_does_not_load_is_refused(self):
        def v1_plugin(directory, zed_home, env, deny_tools=()):  # what Zed wrote before the V2 port: V2 does not load it
            plugin = Path(directory) / "plugins" / "zed-deny.js"
            plugin.parent.mkdir(parents=True, exist_ok=True)
            plugin.write_text('export const ZedDeny = async () => ({ "tool.execute.before": async () => { throw new Error("denied"); } });\n')
            return Path(directory)

        with patch.object(self.z.zeddeny, "opencode_plugin", side_effect=v1_plugin):
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
        # what a review hat is denied, asked for outright (a model that knows its rules does not try them by itself)
        self.ask(session, f"Call the write tool exactly once with the path {self.worktree}/denied.txt and the content x.")
        self.ask(session, "Call the shell tool exactly once with exactly this command: gh pr comment 1 --body x")
        messages = self.api("GET", f"/session/{session}/message")
        calls = self.tool_calls(session)
        denied = lambda c: c["state"]["status"] == "error"
        reason = lambda c: str((c["state"].get("error") or {}).get("message", ""))

        for c in calls:
            self.say("call", f"{c.get('name')} {json.dumps(c['state']['input'])[:160]} -> {c['state']['status']} {reason(c)[:120]}")
        canary = next(c for c in calls if str(c["state"]["input"].get("command", "")).startswith("zed-deny-canary"))
        self.assertTrue(denied(canary) and "canary" in reason(canary), canary)
        self.say("canary", f"{canary['state']['input']['command']} -> {reason(canary)}")

        models = {json.dumps(m["model"], sort_keys=True) for m in messages if m.get("type") == "assistant"}
        self.assertEqual(models, {json.dumps({"providerID": "opencode", "id": "nemotron-3.5-super-vl-free", "variant": "xhigh"}, sort_keys=True)})
        self.say("model of every assistant message", models)
        self.assertEqual((s["model"], s["effort"]), ("opencode/nemotron-3.5-super-vl-free", "xhigh"))
        screen = subprocess.run(["herdr", "agent", "read", s["agent"], "--source", "visible"], capture_output=True, text=True).stdout
        footer = next((line.strip(" ┃") for line in screen.splitlines() if "xhigh" in line), "")
        self.assertIn("xhigh", footer, screen)
        self.say("TUI footer", footer)

        inside = lambda c: self.worktree in (self.worktree / str(c["state"]["input"].get("path", ""))).resolve().parents
        edits = [c for c in calls if c.get("name") in ("write", "edit") and inside(c)]  # the hat may write outside the worktree
        self.assertTrue(edits, "the model made no edit call in the worktree, so the edit rule was not exercised: run again")
        self.assertTrue(all(denied(c) and "zed-deny: editing" in reason(c) for c in edits), edits)
        self.assertFalse((self.worktree / "denied.txt").exists())
        self.say("edit", f"{edits[0]['name']} {edits[0]['state']['input'].get('path')} -> {reason(edits[0])}")
        posts = [c for c in calls if "gh pr comment" in str(c["state"]["input"].get("command", ""))]
        self.assertTrue(posts, "the model made no `gh pr comment` call, so the command rule was not exercised: run again")
        self.assertTrue(all(denied(c) and "`gh pr comment` is denied" in reason(c) for c in posts), posts)
        self.say("command", f"{posts[0]['state']['input']['command']} -> {reason(posts[0])}")

        words = (self.evidence / "words.txt").read_text() if (self.evidence / "words.txt").exists() else ""
        self.say("code words the worker wrote", words.split())
        self.assertIn(self.words["REPO"], words)  # the repository's AGENTS.md
        self.assertIn(self.words["RULES"], words)  # Zed's rules, from the per-session config dir's AGENTS.md ...
        reads = [str(c["state"]["input"].get("path", "")) for c in calls if c.get("name") == "read" and not denied(c)]  # that worked
        self.assertFalse([p for p in reads if "rules" in p or p.endswith("AGENTS.md")], reads)  # ... not from reading a file
        self.assertNotIn(self.words["CLAUDE"], words)  # CLAUDE.md is not read by OpenCode V2
        # the worker's shell commands run in its server: the session's pane must host it, or `zed-employee finish` refuses
        server_pid = self.api("GET", "/info")["pid"]
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
        self.assertEqual(zedlib.git(self.worktree, "status", "--porcelain").stdout.strip(), "")  # the worktree is untouched
        self.assertEqual((self.worktree / "AGENTS.md").read_text(), (self.repo / "AGENTS.md").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
