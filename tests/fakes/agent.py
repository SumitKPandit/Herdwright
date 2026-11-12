"""A scripted agent CLI, the same for every runtime.

A runtime's wrapper (`bin/claude`, `bin/opencode`) only reads its own launch flags and calls `main(launch)`. Everything an agent does here is driven by a script, so a test decides what the "model" does.

The fake herdr runs the wrapper once per thing that happens to the agent, named in $FAKE_AGENT_EVENT:
  start     the CLI was launched in pane $FAKE_AGENT_PANE
  prompt    a prompt was submitted ($FAKE_AGENT_PAYLOAD: the text)
  keys      keys were pressed ($FAKE_AGENT_PAYLOAD: a JSON list)
  advance   time passes: a paused turn goes on (tests call this through the harness)

The script is the JSON file $FAKE_AGENT_SCRIPT: {"<agent name>" or "*": {"start": {...}, "swallow": [n, ..],
"turns": [[act, ..], ..]}}.
  start     {"dialog": "<text>"}: the CLI opens on a dialog and is `blocked` until keys ending in `enter` are pressed
  swallow   prompt numbers (1 = the first) that stay unsent in the input box until `enter` is pressed
  turns     the acts of the turn each prompt starts, in order; a prompt past the last turn ends at once

Acts, one key each:
  {"say": text}                       print text
  {"block": KIND, "text": text}       print a `ZED:<KIND>` block (QUESTION, DONE, BLOCKED)
  {"status": line}                    append a line to the status file named in the first prompt
  {"plan": [step, ..]}                write the plan file the brief names: those steps, then the fixed finish line
  {"tick": n | "all"}                 tick step n, or every step but the finish line
  {"write": path, "text": text}       write a file in the working folder
  {"git": [args]}                     run git there
  {"run": [argv]}                     run a command there; {bin} is Zed's bin folder, {profile} the profile
  {"pause": true}                     keep working until `advance`
  {"dialog": text}                    show a permission prompt and stay `blocked` until keys ending in `enter`
  {"exit": true}                      the CLI quits: the pane stays, the agent is gone
Results of git and run acts are kept in the agent's `results` for the test to read.
"""
import json
import os
import re
import subprocess
from pathlib import Path

import store

FINISH_STEP = "- [ ] Run zed-employee finish and report (ZED:DONE)"


def main(launch):
    """Handle one event for the agent in $FAKE_AGENT_PANE. `launch` is what the wrapper read from its flags:
    {"name", the runtime's screen ("bullet": what it prints before a message, "indent": before the message's other
    lines, "prompt_echo": before the echo of a prompt ("echo_indent": before its other lines, when they differ), "turn_end": the lines it prints when a turn ends, "footer": the
    lines under an idle CLI, "rest": herdr's status for it after a turn, `idle` or `done`), plus whatever the runtime's
    flags carry}."""
    pane_id, event = os.environ["FAKE_AGENT_PANE"], os.environ.get("FAKE_AGENT_EVENT", "start")
    payload = os.environ.get("FAKE_AGENT_PAYLOAD", "")
    if event == "start":
        with store.locked() as state:
            agent = state["panes"][pane_id]["agent"]
            agent["launch"] = launch
            dialog = (script_for(agent).get("start") or {}).get("dialog")
            if dialog:
                agent["dialog"] = dialog
                agent["screen"] += dialog.splitlines()
                store.set_status(agent, "blocked")
    elif event == "prompt":
        submit(pane_id, payload)
    elif event == "keys":
        keys(pane_id, json.loads(payload))
    elif event == "advance":
        with store.locked() as state:
            agent = state["panes"][pane_id]["agent"]
            resumed, agent["paused"] = agent["paused"], False
        if resumed:
            work(pane_id)


def script_for(agent):
    path = os.environ.get("FAKE_AGENT_SCRIPT")
    scripts = json.loads(Path(path).read_text()) if path and Path(path).exists() else {}
    return scripts.get(agent["name"]) or scripts.get("*") or {}


def submit(pane_id, text):
    """A prompt reaches the input box. Returns nothing; the agent's `unsent` says whether it was left there."""
    with store.locked() as state:
        agent = state["panes"][pane_id]["agent"]
        number = len(agent["prompts"]) + len(agent["queued"]) + 1
        if number in script_for(agent).get("swallow", []) and number not in agent["facts"].setdefault("swallowed", []):
            agent["facts"]["swallowed"].append(number)
            agent["unsent"] = text
            return
    take(pane_id, text)


def take(pane_id, text):
    """The prompt is submitted: it starts a turn, or waits its turn behind the one in progress."""
    with store.locked() as state:
        agent = state["panes"][pane_id]["agent"]
        if agent["status"] not in ("idle", "done"):
            agent["queued"].append(text)
            return
        begin_turn(agent, text)
    work(pane_id)


def begin_turn(agent, text):
    agent["prompts"].append(text)
    agent["turn"], agent["step"] = len(agent["prompts"]), 0
    echo, rest = agent["launch"].get("prompt_echo", ""), agent["launch"].get("echo_indent")
    agent["screen"] += [(echo if i == 0 or rest is None else rest) + line for i, line in enumerate(text.splitlines())]
    if len(agent["prompts"]) == 1:
        agent["facts"] |= facts_from(text)
    store.set_status(agent, "working")


def facts_from(prompt):
    """What a worker learns from its first prompt: the files it is told to read and write, found by their shape."""
    paths = [p.rstrip(".,;:)") for p in re.findall(r"/\S+", prompt)]
    home = re.search(r"home is (\S+?);", prompt)

    def first(test):
        return next((p for p in paths if test(p)), None)
    return {"status_file": first(lambda p: p.endswith(".log")), "rules": first(lambda p: p.endswith("-rules.md")),
            "brief": first(lambda p: "/briefs/" in p and p.endswith(".md")), "home": home.group(1) if home else None}


def keys(pane_id, pressed):
    with store.locked() as state:
        agent = state["panes"][pane_id]["agent"]
        agent["keys"].append(pressed)
        enter = bool(pressed) and pressed[-1] == "enter"
        unsent = None
        if agent["dialog"] and enter:
            closed = agent["dialog"].splitlines()  # an answered dialog leaves the screen, as it does in the real CLI
            if agent["screen"][-len(closed):] == closed:
                del agent["screen"][-len(closed):]
            agent["dialog"] = None
            store.set_status(agent, "working" if agent["turn"] else "idle")
            resume = bool(agent["turn"])
        elif agent["unsent"] is not None and enter:
            unsent, agent["unsent"], resume = agent["unsent"], None, False
        else:
            return
    if unsent is not None:
        take(pane_id, unsent)
    elif resume:
        work(pane_id)


def work(pane_id):
    """Run the current turn's acts until it ends, pauses, blocks on a dialog or the CLI exits."""
    while True:
        with store.locked() as state:
            pane = state["panes"].get(pane_id)
            agent = pane and pane["agent"]
            if not agent:
                return
            turns = script_for(agent).get("turns", [])
            acts = turns[agent["turn"] - 1] if 0 < agent["turn"] <= len(turns) else []
            if agent["step"] >= len(acts):
                agent["screen"] += agent["launch"].get("turn_end", [])
                store.set_status(agent, agent["launch"].get("rest", "idle"))
                if not agent["queued"]:
                    return
                begin_turn(agent, agent["queued"].pop(0))
                continue
            act = acts[agent["step"]]
            agent["step"] += 1
            cwd, facts, bullet = pane["cwd"], dict(agent["facts"]), agent["launch"].get("bullet", "")
            indent = agent["launch"].get("indent", "  " if bullet else "")
        out = perform(act, pane_id, cwd, facts)
        with store.locked() as state:
            pane = state["panes"].get(pane_id)
            agent = pane and pane["agent"]
            if not agent:
                return
            if "exit" in act:
                pane["agent"] = None
                return
            for i, line in enumerate(out.get("print", [])):
                agent["screen"].append((bullet if i == 0 else indent) + line)
            if "result" in out:
                agent.setdefault("results", []).append(out["result"])
            if "pause" in act:
                agent["paused"] = True
                return
            if "dialog" in act:
                agent["dialog"] = act["dialog"]
                agent["screen"] += act["dialog"].splitlines()
                store.set_status(agent, "blocked")
                return


def plan_file(facts):
    """The plan file the brief tells its worker to write (the same reading `bin/zed` records at spawn)."""
    m = re.search(r"`([^`\s]+-plan\.md)`", Path(facts["brief"]).read_text())
    return Path(m.group(1))


def perform(act, pane_id, cwd, facts):
    """Do one act. Returns {"print": [lines]} for the screen and {"result": {...}} for a command that ran."""
    if "say" in act:
        return {"print": act["say"].splitlines()}
    if "block" in act:
        return {"print": [f"ZED:{act['block']}", *act.get("text", "").splitlines()]}
    if "status" in act:
        with open(facts["status_file"], "a") as f:
            f.write(act["status"] + "\n")
    elif "plan" in act:
        steps = [f"- [ ] {n}. {step} — done when it is done. ~5m" for n, step in enumerate(act["plan"], 1)]
        path = plan_file(facts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Plan\n\n" + "\n".join([*steps, FINISH_STEP]) + "\n")
    elif "tick" in act:
        path = plan_file(facts)
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            wanted = line != FINISH_STEP if act["tick"] == "all" else line.startswith(f"- [ ] {act['tick']}. ")
            if line.startswith("- [ ] ") and wanted:
                lines[i] = line.replace("- [ ]", "- [x]", 1)
        path.write_text("\n".join(lines) + "\n")
    elif "write" in act:
        target = Path(cwd) / act["write"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(act.get("text", ""))
    elif "git" in act or "run" in act:
        home = Path(facts["home"]) if facts.get("home") else None
        fill = {"bin": str(home / "bin") if home else "", "profile": os.environ.get("ZED_PROFILE_DIR", "")}
        cmd = ["git", *act["git"]] if "git" in act else [a.format(**fill) for a in act["run"]]
        with store.locked() as state:  # herdr knows what runs in the pane: the CLI, whose child the command is
            state["panes"][pane_id]["foreground"] = [os.getpid()]
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        with store.locked() as state:
            if pane_id in state["panes"]:
                state["panes"][pane_id]["foreground"] = []
        return {"print": (r.stdout + r.stderr).splitlines(),
                "result": {"cmd": cmd, "rc": r.returncode, "out": r.stdout + r.stderr}}
    return {}
