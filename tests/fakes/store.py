"""The fake herdr's world: workspaces, panes and the agents in them, kept in one JSON file.

The file is `$FAKE_HERDR_DIR/state.json` (a test names the folder itself). The fake `herdr` executable, the fake agent
CLIs and the tests all change it through `locked()`, a short read-modify-write under a file lock, so a watcher running
in the background and a test acting in the foreground never tear it; `read()` only looks.

  {"next": 1,                                  counter for workspace ids
   "workspaces": {id: {"pane": pane_id, "label": str}},
   "panes": {pane_id: {"workspace_id", "cwd", "env": {..}, "shell_pid": int|None, "foreground": [pid, ..],
                       "agent": None | {see new_agent}}},
   "calls": [[argv..], ..]}                    every herdr command received, oldest first
"""
import contextlib
import fcntl
import json
import os
import subprocess
import uuid
from pathlib import Path

VISIBLE_LINES = 40  # what `agent read --source visible` shows: the bottom of the screen
# what a hat session must not inherit from the coordinator's shell: herdr starts a pane with its own identity
COORDINATOR_ONLY = ("HERDR_PANE_ID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "OPENCODE_SESSION_ID", "OPENCODE_PID")


def home(root=None):
    return Path(root or os.environ["FAKE_HERDR_DIR"])


def read(root=None):
    """The world as it is now. It takes no lock: `locked` replaces the file in one step."""
    path = home(root) / "state.json"
    return json.loads(path.read_text()) if path.exists() else {"next": 1, "workspaces": {}, "panes": {}, "calls": []}


@contextlib.contextmanager
def locked(root=None):
    """The world as a dict; changes made inside the block are saved when it ends."""
    root = home(root)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "state.lock", "w") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        state = read(root)
        yield state
        tmp = root / "state.tmp"
        tmp.write_text(json.dumps(state, indent=1))
        os.replace(tmp, root / "state.json")


def agent_event(pane_id, event, payload="", env=None, root=None):
    """Let the agent CLI in a pane react to `event` (see agent.py): its own executable runs in the pane's folder and
    environment. `env` is the environment of whoever makes it happen (default: this process's)."""
    pane = read(root)["panes"][pane_id]
    agent = pane["agent"]
    env = {k: v for k, v in (os.environ if env is None else env).items() if k not in COORDINATOR_ONLY}
    env |= pane["env"] | {"HERDR_PANE_ID": pane_id, "FAKE_AGENT_PANE": pane_id, "FAKE_AGENT_EVENT": event,
                          "FAKE_AGENT_PAYLOAD": payload}
    subprocess.run([agent["kind"], *agent["argv"]], cwd=pane["cwd"], env=env, check=True)


def new_pane(state, cwd, env=None, label="", shell_pid=None):
    """Add a workspace with its root pane; returns (workspace_id, pane_id)."""
    ws = f"w{state['next']}"
    state["next"] += 1
    pane = f"{ws}:p1"
    state["workspaces"][ws] = {"pane": pane, "label": label}
    state["panes"][pane] = {"workspace_id": ws, "cwd": str(cwd), "env": dict(env or {}), "shell_pid": shell_pid,
                            "foreground": [], "agent": None}
    return ws, pane


def new_agent(name, kind, argv):
    """An agent CLI as herdr sees it. `status` is herdr's agent_status (idle, working, blocked); `seq` goes up on every
    status change. `launch` is what the runtime's wrapper read from its flags, with how its screen looks (set when the
    CLI starts; see bin/claude). The rest is the fake agent's own memory: its `screen`, the `prompts` it received in order, the text
    `unsent` in its input box, the `dialog` it is blocked on, the `keys` it was sent, and where it is in its script."""
    return {"name": name, "kind": kind, "argv": list(argv), "status": "idle", "seq": 0, "session": str(uuid.uuid4()),
            "launch": {}, "screen": [], "prompts": [], "queued": [], "unsent": None, "dialog": None, "keys": [],
            "turn": 0, "step": 0, "paused": False, "facts": {}}


def set_status(agent, status):
    if agent["status"] != status:
        agent["status"] = status
        agent["seq"] += 1


def find_agent(state, ref):
    """(pane_id, pane) of the agent named `ref` (or living in pane `ref`), or (None, None)."""
    for pane_id, pane in state["panes"].items():
        agent = pane["agent"]
        if agent and ref in (agent["name"], pane_id):
            return pane_id, pane
    return None, None


def agent_record(pane_id, pane):
    """The agent as `herdr agent list` / `agent get` print it."""
    agent = pane["agent"]
    return {"agent": agent["kind"], "name": agent["name"], "pane_id": pane_id, "workspace_id": pane["workspace_id"],
            "agent_status": agent["status"], "state_change_seq": agent["seq"], "cwd": pane["cwd"],
            "agent_session": {"agent": agent["kind"], "kind": "id", "source": f"herdr:{agent['kind']}", "value": agent["session"]}}


def pane_record(pane_id, pane):
    """The pane as `herdr pane get` / `pane list` print it."""
    out = {"pane_id": pane_id, "workspace_id": pane["workspace_id"], "cwd": pane["cwd"]}
    if pane["agent"]:
        out |= {k: v for k, v in agent_record(pane_id, pane).items() if k in ("agent", "agent_status", "agent_session")}
    return out
