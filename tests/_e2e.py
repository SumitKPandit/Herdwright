"""The end-to-end harness: a throwaway profile, a throwaway git repo, and the fakes in tests/fakes/ first on PATH.

A `World` is everything one test needs. The real `bin/zed` runs as a subprocess against it, as a coordinator session
would run it: in a (fake) herdr pane, with its own agent session. Nothing here reads or writes the real profile, the
real herdr or the network: the environment of every command is built from scratch (see `World.env`).
"""
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
FAKES = HOME / "tests" / "fakes"
sys.path.insert(0, str(FAKES))
import store  # noqa: E402

ZED = str(HOME / "bin" / "zed")
RUN_ZED = [sys.executable, str(FAKES / "no_wait.py"), ZED]  # the real `bin/zed`, its waits cut short
REPO = "demo"
BASE = "main"
# never inherited from the shell that runs the tests: a worker session carries all of these
FOREIGN = ("ZED_", "CLAUDE", "OPENCODE", "HERDR", "GIT_", "FAKE_")
FILL = re.compile(r"<FILL\b[^>\n]*>")  # what `bin/zed brief` leaves for the coordinator to write


def ask(*questions):
    """Acts of a worker asking: one status line per question, then the ZED:QUESTION block in the same words."""
    return [*({"status": f"needs-decision: {q}"} for q in questions),
            {"block": "QUESTION", "text": "\n".join(f"{n}. {q}" for n, q in enumerate(questions, 1))}]


def done(summary, qa, pr="not raised", branch="task/demo"):
    """Acts of a worker reporting: the status line, then the ZED:DONE block."""
    url = pr if pr.startswith("http") else "none"
    return [{"status": f"done: {summary}; pr={url}; qa={qa}"},
            {"block": "DONE", "text": f"Summary: {summary}\nBranch: {branch}  Commits: abc1234\nPR: {pr}\n"
                                      f"QA: {qa}\nFollow-ups: none\nLearnings: none"}]


def clean_env(home, **extra):
    """An environment built from scratch for a command under test: the fakes first on PATH, a throwaway HOME, git
    without the user's own config, and none of the caller's Zed, Claude, herdr or git variables."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(FOREIGN)}
    return env | {"PATH": f"{FAKES / 'bin'}{os.pathsep}{env['PATH']}", "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1",
                  "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                  "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                  "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"} | extra


_template = None  # the folder every World copies its git repo and origin from, built once per test run


def template():
    """A folder with the demo repo (one commit on BASE, pushed) and its bare origin."""
    global _template
    if _template is None:
        _template = tempfile.TemporaryDirectory()  # removed when the test run ends
        root = Path(_template.name).resolve()

        def git(*args, cwd):
            subprocess.run(["git", *args], cwd=cwd, env=clean_env(root), check=True, capture_output=True)
        (root / REPO).mkdir()
        git("init", "-q", "-b", BASE, cwd=root / REPO)
        (root / REPO / "README.md").write_text("demo\n")
        git("add", "-A", cwd=root / REPO)
        git("commit", "-q", "-m", "Start", cwd=root / REPO)
        git("init", "-q", "--bare", "origin.git", cwd=root)
        git("remote", "add", "origin", str(root / "origin.git"), cwd=root / REPO)
        git("push", "-q", "origin", BASE, cwd=root / REPO)
    return Path(_template.name).resolve()


class World:
    def __init__(self, case):
        self.case = case
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.profile = self.root / "profile"
        self.herdr = self.root / "herdr"
        self.repo = self.root / "repos" / REPO
        self.worktrees = self.root / "worktrees"
        self.script_path = self.root / "agent-script.json"
        self.coordinators = {}
        for d in (self.profile / "config", self.herdr, self.root / "home"):
            d.mkdir(parents=True)
        (self.profile / "profile.md").write_text("# Throwaway test profile\n")
        (self.profile / "config" / "projects.json").write_text(json.dumps(
            {"worktree_root": str(self.worktrees), "repos": {REPO: {"path": str(self.repo), "role": "demo repo"}}}))
        shutil.copytree(template() / REPO, self.repo)
        shutil.copytree(template() / "origin.git", self.root / "origin.git")
        self.git("remote", "set-url", "origin", str(self.root / "origin.git"))
        self.me = self.coordinator("zed")
        case.assertEqual(shutil.which("herdr", path=self.env()["PATH"]), str(FAKES / "bin" / "herdr"))

    # ------------------------------------------------------------ running things

    def env(self, coordinator=None):
        """The environment of a command run by `coordinator` (default: the first one)."""
        return clean_env(self.root / "home", ZED_PROFILE_DIR=str(self.profile), FAKE_HERDR_DIR=str(self.herdr),
                         FAKE_AGENT_SCRIPT=str(self.script_path)) | (coordinator or getattr(self, "me", {}))

    def git(self, *args, cwd=None, check=True):
        r = subprocess.run(["git", *args], cwd=cwd or self.repo, env=self.env(), capture_output=True, text=True)
        if check:
            self.case.assertEqual(r.returncode, 0, f"git {' '.join(args)}\n{r.stdout}{r.stderr}")
        return r

    def zed(self, *args, coordinator=None, check=True, timeout=90):
        """Run `bin/zed <args>` as a coordinator session. With check, a failure fails the test with the output."""
        r = subprocess.run([*RUN_ZED, *args], env=self.env(coordinator), capture_output=True, text=True,
                           timeout=timeout, cwd=self.root)
        if check:
            self.case.assertEqual(r.returncode, 0, f"bin/zed {' '.join(args)}\n{r.stdout}{r.stderr}")
        return r

    def coordinator(self, name):
        """A coordinator session in its own pane of the fake herdr. Returns the environment that identifies it, as
        Claude Code and herdr set it in a real pane."""
        with store.locked(self.herdr) as state:
            _, pane = store.new_pane(state, HOME, label=name, shell_pid=os.getpid())
            agent = store.new_agent(name, "claude", [])
            agent["status"] = "working"
            state["panes"][pane]["agent"] = agent
        self.coordinators[name] = {"HERDR_PANE_ID": pane, "CLAUDE_CODE_SESSION_ID": agent["session"],
                                   "CLAUDE_PID": str(os.getpid())}
        return self.coordinators[name]

    # ------------------------------------------------------------ the loop's steps

    def start(self, coordinator=None):
        return self.zed("start", coordinator=coordinator)

    def task(self, title="Add a greeting", **fields):
        out = self.zed("task", "add", f"title={title}", *(f"{k}={v}" for k, v in fields.items())).stdout
        return int(out.strip().lstrip("T"))

    def brief(self, tid, branch, fill="none", name=None):
        """Scaffold the brief with the real `bin/zed brief`; `fill` replaces every <FILL: …> (None leaves them). With
        `name` (a review or QA session's brief) it is a brief of its own, with its own plan file."""
        self.zed("brief", f"T{tid}", f"repo={REPO}", f"base={BASE}", f"branch={branch}", "force")
        path = self.profile / "briefs" / f"T{tid}-{REPO}.md"
        text = path.read_text() if fill is None else FILL.sub(fill, path.read_text())
        if name:
            path = path.with_name(f"T{tid}-{REPO}-{name}.md")
            text = text.replace(f"T{tid}-{REPO}-plan.md", f"T{tid}-{REPO}-{name}-plan.md")
        path.write_text(text)
        return path

    def script(self, **agents):
        """What each agent does, by agent name (see fakes/agent.py); `default=` is for any other agent."""
        scripts = {("*" if name == "default" else name.replace("_", "-")): s for name, s in agents.items()}
        self.script_path.write_text(json.dumps(scripts))

    def spawn(self, tid, branch, hat="build", check=True, brief=None):
        """`bin/zed spawn`; returns the JSON line it prints (session, agent, pane, worktree) when it succeeds."""
        brief = brief or self.profile / "briefs" / f"T{tid}-{REPO}.md"
        r = self.zed("spawn", f"T{tid}", f"repo={REPO}", f"base={BASE}", f"branch={branch}", f"brief={brief}",
                     f"hat={hat}", check=check)
        if r.returncode:
            return r
        return json.loads(next(line for line in r.stdout.splitlines() if line.startswith("{")))

    def started(self, branch="task/demo", turns=(), **script):
        """A task with a build worker already spawned on `branch`, whose turns do `turns`. Returns the spawn record."""
        tid = self.task()
        self.brief(tid, branch)
        self.script(default={"turns": list(turns), **script})
        return self.spawn(tid, branch) | {"tid": tid}

    # ------------------------------------------------------------ the watcher

    def watch_once(self):
        """`bin/zed watch --once`: returns when the watcher has something for the coordinator (the process result).
        A watcher with nothing to say never returns: that fails the test at the time limit."""
        try:
            return self.zed("watch", "--once", "0.05", timeout=30)
        except subprocess.TimeoutExpired as e:
            self.case.fail(f"the watcher reported nothing in {e.timeout} s; it printed:\n{e.stdout or ''}{e.stderr or ''}")

    def watch_state_changes(self, count, running=None, timeout=30):
        """Wait until the watcher has finished `count` more looks at the sessions (it saves its state after each)."""
        state = self.profile / ".cache" / "watch-state.json"
        stamp = lambda: state.stat().st_mtime_ns if state.exists() else 0  # noqa: E731
        seen, last, deadline = 0, stamp(), time.time() + timeout
        while seen < count and (running is None or running.poll() is None):
            self.case.assertLess(time.time(), deadline, "the watcher did not look in time")
            time.sleep(0.02)
            if stamp() != last:
                seen, last = seen + 1, stamp()

    def watch_ticks(self, ticks=3, coordinator=None):
        """Let a watcher look at every session `ticks` times, then stop it. Returns what it printed."""
        p = subprocess.Popen([*RUN_ZED, "watch", "0.05"], env=self.env(coordinator), cwd=self.root,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            self.watch_state_changes(ticks, running=p)
        finally:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
            out, _ = p.communicate(timeout=10)
        return out

    def ack_all(self):
        self.zed("ack", "--all")

    # ------------------------------------------------------------ reading the record and the world

    def rows(self, sql, *args):
        conn = sqlite3.connect(f"file:{self.profile / 'db' / 'zed.db'}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()

    def session(self, sid):
        return self.rows("SELECT * FROM sessions WHERE id=?", int(str(sid).lstrip("S")))[0]

    def events(self, kind):
        return [r["text"] for r in self.rows("SELECT text FROM events WHERE kind=? ORDER BY id", kind)]

    def wakes(self):
        """The wake queue: every event the watcher has raised for the coordinator."""
        path = self.profile / ".cache" / "wake-queue.json"
        return json.loads(path.read_text())["events"] if path.exists() else []

    def agent(self, name):
        """The fake agent's state (None once its pane is gone or the CLI quit)."""
        _, pane = store.find_agent(store.read(self.herdr), name)
        return pane["agent"] if pane else None

    def herdr_calls(self, *prefix):
        """The herdr commands received so far that start with `prefix`."""
        return [c for c in store.read(self.herdr)["calls"] if c[:len(prefix)] == list(prefix)]

    # ------------------------------------------------------------ making things happen

    def advance(self, name):
        """Time passes for an agent paused mid-turn: it goes on with its script."""
        pane_id, _ = store.find_agent(store.read(self.herdr), name)
        store.agent_event(pane_id, "advance", env=self.env(), root=self.herdr)

    def kill_pane(self, name):
        """The agent's pane disappears (the user closed it, or herdr lost it)."""
        with store.locked(self.herdr) as state:
            pane_id, pane = store.find_agent(state, name)
            del state["panes"][pane_id]
            del state["workspaces"][pane["workspace_id"]]

    def age_sessions(self, minutes=10):
        """Stand in for waiting: the watcher treats a session younger than three minutes as still reading its brief."""
        conn = sqlite3.connect(self.profile / "db" / "zed.db")
        with conn:
            conn.execute("UPDATE sessions SET created_at = strftime('%Y-%m-%d %H:%M', created_at, ?)", (f"-{minutes} minutes",))
        conn.close()

    def age_lapses(self, seconds=60):
        """Stand in for waiting: the watcher nudges a worker that stays idle with no report for half a minute. Call it
        while no watcher runs (the watcher keeps this file between its runs)."""
        path = self.profile / ".cache" / "watch-alerts.json"
        alerts = json.loads(path.read_text())
        for seen in alerts.values():
            if isinstance(seen, dict) and "lapse" in seen:
                seen["lapse"]["since"] -= seconds
        path.write_text(json.dumps(alerts))


class LoopCase(unittest.TestCase):
    """A test case with a fresh world whose coordinator already holds the profile lock."""

    def setUp(self):
        self.w = World(self)
        self.w.zed("lock", "acquire")
