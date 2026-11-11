"""A hat's `deny` policy (templates/hats/<hat>/capabilities.json). The decision lives here; the hook script
bin/zed-deny only feeds it a tool call. It runs as a PreToolUse command hook in a per-session settings file
(`claude --settings`) or from an OpenCode V2 plugin (`opencode_plugin`); git itself is guarded by bin/zed-guard, which spawn installs in the worktree.
"""
import json
import os
import re
import subprocess
import sys
from itertools import dropwhile, takewhile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zedgates  # noqa: E402
import zedshell  # noqa: E402
from zedshell import unresolved  # noqa: E402

# Tool names are compared in lower case. Claude Code: Edit, Write, NotebookEdit, MultiEdit, Bash, Agent, Task. OpenCode V2
# (`ctx.tool.list()` on v2.2.7): edit and write (`path`), patch (`patchText`), shell (`command`, `workdir`), subagent.
EDIT_TOOLS = {"edit", "write", "notebookedit", "multiedit", "patch"}
PATH_KEYS = ("file_path", "path", "notebook_path")
# OpenCode's patch names its files in marker lines of `patchText` (opencode.ai/docs/tools, read 2026-11-11)
PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.M)
SHELL_TOOLS = {"bash", "shell"}
SUBAGENT_TOOLS = {"agent", "task", "subagent"}
# The shell command spawn asks a new OpenCode worker to run: every hat denies it, so a call that comes back denied with
# this reason proves the guard is live in that worker's server (a plugin that fails to load blocks nothing).
CANARY_COMMAND = "zed-deny-canary"
PRETOOL_CANARY = "zed-pretool-canary"  # the coordinator's: bin/zed-pretool denies it in every session
# What a hat session's identity rests on (`zedemployee.caller_session`): a Bash command may not set or unset them
IDENTITY_VARS = ("ZED_SESSION", "ZED_HAT", "ZED_PROFILE_DIR", "HERDR_PANE_ID")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
ENV_SETTERS = {"export", "declare", "typeset", "readonly", "local", "unset", "env", "set"}
WRAPPERS = ("builtin", "command", "exec", "nohup", "time", "nice")
LOOK_THROUGH = WRAPPERS + ("xargs", "find", "sudo", "doas", "setsid", "timeout", "stdbuf", "ionice", "watch", "busybox", "script")  # run the command that follows


def glob_regex(pattern):
    """`**` matches anything including `/`; `*` and `?` stay inside one path segment."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        else:
            c = pattern[i]
            out += "[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c)
            i += 1
    return re.compile(out + r"\Z")


def path_matches(p, pattern, worktree, profile):
    """Whether the resolved path `p` matches `pattern`: one beginning `profile/` is relative to the profile folder, any
    other to the worktree. `{session}` in a pattern is this session's id (ZED_SESSION)."""
    pattern = pattern.replace("{session}", os.environ.get("ZED_SESSION", "-"))
    base, rel_pattern = (Path(profile), pattern[len("profile/"):]) if pattern.startswith("profile/") else (Path(worktree), pattern)
    base = Path(os.path.realpath(base))
    return (p == base or base in p.parents) and bool(glob_regex(rel_pattern).match(str(p.relative_to(base))))


def denied_path(path, patterns, worktree, profile, cwd, exceptions=()):
    """The pattern that denies `path`, or None. A pattern beginning `profile/` is relative to the profile folder,
    any other to the worktree; paths outside both (scratch, evidence directories, /tmp) are never denied.
    A path matching an exception is allowed."""
    p = Path(os.path.expanduser(str(path)))
    p = Path(os.path.realpath(p if p.is_absolute() else Path(cwd) / p))
    for pattern in patterns:
        if path_matches(p, pattern, worktree, profile) and not any(path_matches(p, e, worktree, profile) for e in exceptions):
            return pattern
    return None


def holds_profile(repo, profile, cwd):
    """True when `repo` is the checkout the profile lives in (any folder of it, or a folder above it): `git -C <that> commit` would
    commit profile files, which a hat that may not edit them must not do."""
    p = Path(os.path.expanduser(str(repo)))
    p = Path(os.path.realpath(p if p.is_absolute() else Path(cwd) / p))
    profile = Path(os.path.realpath(profile))
    if p == profile or p in profile.parents:
        return True
    top = lambda d: subprocess.run(["git", "-C", str(d), "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    return p.is_dir() and top(p) != "" and os.path.realpath(top(p)) == os.path.realpath(top(profile))


def command_denied(words, prefixes):
    """The denied prefix a simple command starts with (the program is compared by basename), or None."""
    for prefix in prefixes:
        want = prefix.split()
        if len(words) >= len(want) and Path(words[0]).name == want[0] and words[1:len(want)] == want[1:]:
            return prefix
    return None


def identity_tampering(words):
    """A reason when the simple command `words` sets or unsets one of IDENTITY_VARS (inline `VAR=x cmd`, `export`,
    `unset`, `env`, `env -uVAR`, `--unset=VAR`, ...), clears the environment (`env -i`, which would make a hat shell look
    like Zed's) or asks `zed-employee finish` for `--as-zed` (a hat session never needs it), else None. Programs that run
    another (`sudo`, `xargs`, `find -exec`, ...) are looked through; `bash -c STRING` and `eval STRING` are parsed."""
    names = "/".join(IDENTITY_VARS)
    named = lambda w: any(w == v or w.startswith(v + "=") for v in IDENTITY_VARS)
    mentions = lambda w: any(v in w for v in IDENTITY_VARS)
    unnamed = lambda w: re.search(r"[$`{]", w.split("=", 1)[0])  # a variable name the shell expands: `$V=x`, `ZED_{SESSION,HAT}` (the tokenizer splits it at the comma)
    assignment = lambda w: re.fullmatch(r"\w+=.*", w)
    flat = [x for w in words for x in w.split()]  # `env -S 'zed-employee finish --as-zed'` is one word
    if "finish" in flat and any(len(w) >= 3 and "--as-zed".startswith(w.split("=")[0]) for w in flat):  # argparse accepts `--as`
        return "`zed-employee finish --as-zed` is denied to a hat session: it finishes only itself"
    if any(named(w) for w in takewhile(assignment, words)):
        return f"setting {names} is denied: they tell Zed which session this is"
    words = list(dropwhile(assignment, words))
    wrapped = False
    while words and (Path(words[0]).name in WRAPPERS or words[0] in ("{", "then", "do", "else")):
        wrapped, words = wrapped or Path(words[0]).name in WRAPPERS, words[1:]
    if not words:
        return None
    name = Path(words[0]).name
    args = list(takewhile(lambda w: w.startswith("-") or "=" in w, words[1:])) if name == "env" else words[1:]  # env: up to its command
    if name in ENV_SETTERS and (any(mentions(w) or unnamed(w) for w in args) or (name == "env" and any(mentions(w) for w in words[1:]))
                                or (name in ("unset", "env") and any(unresolved(w) and (name == "unset" or re.search(r"-u|--unset", prev))
                                                                      for prev, w in zip(words, words[1:])))):
        return f"`{name}` on {names} (or on a name the shell expands) is denied: they tell Zed which session this is"
    if name == "env" and any(w in ("-", "--ignore-environment") or re.fullmatch(r"-[A-Za-z]*i[A-Za-z]*", w) for w in words[1:]):
        return "`env -i` is denied: it clears the variables that tell Zed which session this is"
    if name in LOOK_THROUGH or wrapped:  # a wrapper's own flags (`nice -n 5 env ...`, `time -p`, `exec -a x`) hide the command: scan for it
        for i, w in enumerate(words[1:], 1):
            if Path(w).name in ENV_SETTERS | SHELLS | {"eval"} and (problem := identity_tampering(words[i:])):
                return problem
    if name in SHELLS:
        flag = next((i for i, w in enumerate(words) if i and re.fullmatch(r"-\w*c\w*", w)), None)
        inner = words[flag + 1] if flag is not None and flag + 1 < len(words) else ""
    else:
        inner = " ".join(words[1:]) if name == "eval" else ""
    try:
        return next((p for sub, _ in zedshell.split_commands(zedshell.tokens(inner)) if (p := identity_tampering(sub))), None)
    except ValueError:  # unbalanced quotes: fail open, like the rest of this hook
        return None


def shell_problem(cmd, manifest, worktree, profile, cwd):
    deny = manifest["deny"]
    patterns, exceptions = deny["edit"], deny.get("except", [])
    try:
        cmds = zedshell.scoped_commands(zedshell.tokens(cmd))
    except ValueError:
        return None  # unbalanced quotes: fail open, like the other hooks
    read_only = "**" in deny["edit"]  # a hat that may not edit at all may not commit either
    dirs = {(): cwd}  # where each shell of the command line stands: a `cd` in a subshell or a pipe does not move the others
    for words, scope in cmds:
        cwd = zedshell.scope_dir(dirs, scope)
        if problem := identity_tampering(words):
            return problem
        words_before = [w for w in words if re.fullmatch(r"CDPATH=.*", w)] if words else []
        while words and re.fullmatch(r"\w+=.*", words[0]):
            words = words[1:]
        if not words:
            continue
        if re.fullmatch(r"CDPATH=.*", words_before[0] if words_before else ""):
            cwd = dirs[scope] = None  # `CDPATH=x cd y` may land anywhere
        while words and (Path(words[0]).name in ("builtin", "command") or words[0] in ("{", "then", "do", "else")):
            words = words[1:]  # a compound command's opener leaves the next word the command
        if not words:
            continue  # only an opener (`do`, `then`) before the next `;`
        if Path(words[0]).name in zedshell.CD_COMMANDS:
            # follow `cd <dir> && ...` so later relative targets resolve where the shell will; a target the shell
            # expands (`cd $X`, `cd -`, `popd`) leaves the cwd unknown: later relative targets cannot be checked
            dirs[scope] = zedshell.cd_target(words, cwd)
            continue
        if prefix := command_denied(words, deny["commands"]):
            return f"`{prefix}` is denied to this hat: anything outward goes to Zed as a draft"
        if prefix := command_denied(words, deny.get("gated", [])):
            if reason := zedgates.gate_reason(worktree):
                return f"`{prefix}` waits for a clean review and qa of this branch: {reason}"
        targets = [words[i + 1] for i, w in enumerate(words[:-1]) if w in zedshell.REDIRECTS and not words[i + 1].startswith("&")]
        name = Path(words[0]).name
        if name in zedshell.FILE_WRITERS or (name in ("sed", "perl") and any(re.fullmatch(r"-\w*i\w*", w) for w in words[1:])):
            targets += [w for w in words[1:] if not w.startswith("-")]
        for t in targets:
            if unresolved(t) or (cwd is None and not (Path(os.path.expanduser(t)).is_absolute())):
                return f"writing {t} cannot be checked (the shell expands it or the directory is unknown): use a literal absolute path"
            if pattern := denied_path(t, patterns, worktree, profile, cwd, exceptions):
                return f"writing {t} is denied to this hat ({pattern})"
        if name == "git" and read_only:
            sub, repo = zedshell.git_target(words, cwd)
            if sub in zedshell.GIT_WRITE and (repo is None or unresolved(repo)):
                return f"`git {sub}` cannot be checked (the directory is unknown): use `git -C <absolute path>`"
            if sub in zedshell.GIT_WRITE and (denied_path(repo, ["**", "profile/**"], worktree, profile, cwd)
                                              or holds_profile(repo, profile, cwd)):
                return f"`git {sub}` in the worktree or the profile is denied: this hat does not change the repo"
    return None


def edit_paths(tool_input):
    """The files an edit tool call touches: the path in its input, or the files named in a patch's text."""
    if not isinstance(tool_input, dict):
        return []
    if isinstance(tool_input.get("patchText"), str):
        return [m.group(1).strip() for m in PATCH_FILE.finditer(tool_input["patchText"])]
    path = next((tool_input[k] for k in PATH_KEYS if tool_input.get(k)), None)
    return [path] if path else []


def decide(payload, manifest, worktree, profile):
    """Reason to deny this tool call for a session wearing the hat `manifest` describes, or None to allow it.
    An internal error denies: a guard that cannot decide must not wave the call through."""
    try:
        return _decide(payload, manifest, worktree, profile)
    except Exception as e:
        return f"the call could not be checked ({type(e).__name__}: {e}), so it is denied"


def _decide(payload, manifest, worktree, profile):
    tool = str(payload.get("tool_name", "")).lower()
    inp = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    if tool in EDIT_TOOLS:
        paths = edit_paths(inp)
        if not paths:
            return "an edit that names no file cannot be checked, so it is denied" if "**" in manifest["deny"]["edit"] else None
        for path in paths:
            if pattern := denied_path(path, manifest["deny"]["edit"], worktree, profile, cwd, manifest["deny"].get("except", [])):
                return f"editing {path} is denied to this hat ({pattern})"
    elif tool in SHELL_TOOLS:
        inp = inp if isinstance(inp, dict) else {}
        cmd = inp.get("command", "")
        if cmd.split()[:1] == [CANARY_COMMAND]:
            return f"canary: `{cmd.strip()}` is denied to every hat, so the guard is live"
        if inp.get("workdir"):  # OpenCode's shell runs in `workdir`, relative to the session's directory
            cwd = os.path.join(cwd, os.path.expanduser(inp["workdir"]))
        return shell_problem(cmd, manifest, worktree, profile, cwd)
    elif tool in SUBAGENT_TOOLS and manifest["subagents"] == "none":
        return "this hat starts no subagents"
    return None


# ---------------------------------------------------------------- rendering


def hook_command(zed_home):
    return str(Path(zed_home) / "bin" / "zed-deny")


def stop_command(zed_home):
    return f"{Path(zed_home) / 'bin' / 'zed-employee'} stop-guard"


def claude_settings(zed_home, deny_tools=()):
    """Contents of the per-session `claude --settings` file (additive to the project's own settings). `deny_tools` are the
    tool names a hat session may not call (`worker.deny_tools` in the profile's config/settings.json), e.g. a connector's
    write tools: only Zed, with the user present, posts."""
    return {"permissions": {"deny": list(deny_tools)},
            "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": hook_command(zed_home), "timeout": 10}]}],
                      "Stop": [{"hooks": [{"type": "command", "command": stop_command(zed_home), "timeout": 10}]}]}}


def opencode_plugin(directory, zed_home, env, deny_tools=()):
    """A per-session OpenCode config directory (the worker's server runs with OPENCODE_CONFIG_DIR pointing at it) whose
    V2 plugin runs bin/zed-deny before every tool call and throws its reason when the call is denied; a throw stops the
    call. `env` (ZED_HAT, ZED_SESSION, ZED_WORKTREE, ZED_PROFILE_DIR) is written into the plugin, so the policy does
    not depend on how the server was started; `deny_tools` are tool names no worker may call (see `claude_settings`).
    The same check runs when the server starts a shell for any other caller (its `session.shell` and `/api/shell`
    routes pass no tool hook but do pass the shell hook), and the server's password is taken out of every shell's
    environment. Not stopped, and said so in the README: a worker that reads the password from the process list and
    uses the server's terminal route (`/api/pty` passes no hook), starts a server of its own, or runs a script.
    A shell tool call passes both hooks, so zed-deny runs twice for it: the second run is what checks the routes.
    A zed-deny that cannot run denies. OpenCode has no hook that can refuse a turn end, so there is no stop-guard here;
    a finish that was skipped still shows up as `done:` without a recorded finish. V1 is not supported: a V1 server
    ignores this file, and spawn's canary then refuses the worker."""
    plugin = Path(directory) / "plugins" / "zed-deny" / "index.js"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text(f"""import {{ Plugin }} from "@opencode/plugin";
import {{ spawnSync }} from "node:child_process";

const ENV = {json.dumps(dict(env))};
const DENY_TOOLS = {json.dumps(list(deny_tools))};

export default Plugin.define({{
  id: "zed.deny",
  async setup(ctx) {{
    const check = (tool, input) => {{
      const res = spawnSync({json.dumps(hook_command(zed_home))}, [], {{
        input: JSON.stringify({{ tool_name: tool, tool_input: input, cwd: ctx.location.directory }}),
        encoding: "utf-8",
        env: {{ ...process.env, ...ENV }},
      }});
      if (res.status !== 0) throw new Error((res.stderr || res.stdout || "zed-deny could not run").trim());
    }};
    await ctx.tool.hook("execute.before", (event) => {{
      if (DENY_TOOLS.includes(event.tool)) throw new Error(`zed-deny: the tool ${{event.tool}} is denied to a worker`);
      check(event.tool, event.input);
    }});
    await ctx.shell.hook("create.before", (event) => {{
      delete event.env.OPENCODE_PASSWORD;
      check("shell", {{ command: event.command, workdir: event.cwd }});
    }});
  }},
}});
""")
    return Path(directory)


def coordinator_plugin(directory, zed_home, env):
    """The coordinator's OpenCode config directory gets a V2 plugin that runs bin/zed-pretool before every tool call,
    with the call's session id, and throws its reason when the call is denied. zed-pretool acts only on the session
    holding the profile lock (and on its canary), so the plugin is inert anywhere else. `env` (ZED_PROFILE_DIR) is
    written into the plugin. A zed-pretool that cannot run denies: `bin/zed coordinator` proved it ran once (the
    canary), so a later failure is a broken install, not a call to wave through."""
    plugin = Path(directory) / "plugins" / "zed-pretool" / "index.js"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text(f"""import {{ Plugin }} from "@opencode/plugin";
import {{ spawnSync }} from "node:child_process";

const ENV = {json.dumps(dict(env))};

export default Plugin.define({{
  id: "zed.pretool",
  async setup(ctx) {{
    await ctx.tool.hook("execute.before", (event) => {{
      const res = spawnSync({json.dumps(str(Path(zed_home) / "bin" / "zed-pretool"))}, [], {{
        input: JSON.stringify({{ tool_name: event.tool, tool_input: event.input, cwd: ctx.location.directory, session_id: event.sessionID }}),
        encoding: "utf-8",
        env: {{ ...process.env, ...ENV }},
      }});
      if (res.status !== 0) throw new Error((res.stderr || "zed-pretool could not run").trim());
      const decision = res.stdout.trim() ? JSON.parse(res.stdout).hookSpecificOutput : null;
      if (decision && decision.permissionDecision === "deny") throw new Error(decision.permissionDecisionReason);
    }});
  }},
}});
""")
    return Path(directory)
