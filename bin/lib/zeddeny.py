"""A hat's `deny` policy (templates/employees/hats/<hat>/capabilities.json), decided once and rendered per runtime
(design R2). The decision lives here; the hook script bin/zed-deny only feeds it a tool call.

  Claude Code   a PreToolUse command hook in a per-session settings file (`claude --settings`)
  Copilot CLI   a PreToolUse hook in a per-session plugin (`--plugin-dir`) and `--deny-tool shell(<prefix>)` flags
  both          git itself is guarded by bin/zed-guard, which spawn installs in the worktree

Facts about both runtimes' hook protocols are in docs/runtimes/*.md.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zedgates  # noqa: E402
import zedshell  # noqa: E402

EDIT_TOOLS = {"edit", "write", "notebookedit", "multiedit", "create", "str_replace", "str_replace_editor", "apply_patch"}
PATH_KEYS = ("file_path", "path", "notebook_path", "filePath")
PATCH_FILE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.M)
SUBAGENT_TOOLS = {"agent", "task"}
EMPLOYEE_DATA = "profile/employees/**"  # every hat: employee records change only through `zed-employee`


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
    A path matching an exception is allowed, except under the employee records, which no exception reaches."""
    p = Path(os.path.expanduser(str(path)))
    p = Path(os.path.realpath(p if p.is_absolute() else Path(cwd) / p))
    for pattern in patterns:
        if path_matches(p, pattern, worktree, profile) and not (
                pattern != EMPLOYEE_DATA and any(path_matches(p, e, worktree, profile) for e in exceptions)):
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


UNRESOLVED = re.compile(r"[$`]|^~[^/]")


def unresolved(path):
    """True when the shell would expand `path` (variable, command substitution, `~user`), which a hook cannot follow."""
    return bool(UNRESOLVED.search(str(path)))


def command_denied(words, prefixes):
    """The denied prefix a simple command starts with (the program is compared by basename), or None."""
    for prefix in prefixes:
        want = prefix.split()
        if len(words) >= len(want) and Path(words[0]).name == want[0] and words[1:len(want)] == want[1:]:
            return prefix
    return None


def shell_problem(cmd, manifest, worktree, profile, cwd):
    deny = manifest["deny"]
    patterns, exceptions = [EMPLOYEE_DATA, *deny["edit"]], deny.get("except", [])
    try:
        cmds = zedshell.split_commands(zedshell.tokens(cmd))
    except ValueError:
        return None  # unbalanced quotes: fail open, like the other hooks
    read_only = "**" in deny["edit"]  # a hat that may not edit at all may not commit either
    for words, _ in cmds:
        words_before = [w for w in words if re.fullmatch(r"CDPATH=.*", w)] if words else []
        while words and re.fullmatch(r"\w+=.*", words[0]):
            words = words[1:]
        if not words:
            continue
        if re.fullmatch(r"CDPATH=.*", words_before[0] if words_before else ""):
            cwd = None  # `CDPATH=x cd y` may land anywhere
        while words and (Path(words[0]).name in ("builtin", "command") or words[0] in ("{", "then", "do", "else")):
            words = words[1:]  # a compound command's opener leaves the next word the command
        if words and Path(words[0]).name in ("cd", "pushd", "popd"):
            # follow `cd <dir> && ...` so later relative targets resolve where the shell will; a target the shell
            # expands (`cd $X`, `cd -`, `popd`) leaves the cwd unknown: later relative targets cannot be checked
            args = [w for w in words[1:] if w != "--" and not re.fullmatch(r"-[LPe@]+", w)]
            if args and cwd is not None and not unresolved(args[0]) and args[0] != "-":
                d = Path(os.path.expanduser(args[0]))
                cwd = os.path.realpath(d if d.is_absolute() else Path(cwd) / d)
            elif args or Path(words[0]).name in ("popd", "cd"):
                cwd = None  # a bare `cd` goes home
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
    """The files an edit tool call touches. Claude passes a dict with a path; Copilot's `Edit` passes an apply_patch
    text (verified 2026-10-27) whose `*** Add|Update|Delete File:` and `*** Move to:` lines name them."""
    if isinstance(tool_input, str):
        return [m.group(1).strip() for m in PATCH_FILE.finditer(tool_input)]
    path = next((tool_input[k] for k in PATH_KEYS if tool_input.get(k)), None)
    return [path] if path else []


def decide(payload, manifest, worktree, profile):
    """Reason to deny this tool call for an employee wearing the hat `manifest` describes, or None to allow it.
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
            if pattern := denied_path(path, [EMPLOYEE_DATA, *manifest["deny"]["edit"]], worktree, profile, cwd,
                                      manifest["deny"].get("except", [])):
                return f"editing {path} is denied to this hat ({pattern})" + \
                       ("; use `zed-employee remember|refresh|drop`" if pattern == EMPLOYEE_DATA else "")
    elif tool == "bash":
        return shell_problem(inp.get("command", "") if isinstance(inp, dict) else "", manifest, worktree, profile, cwd)
    elif tool in SUBAGENT_TOOLS and manifest["subagents"] == "none":
        return "this hat starts no subagents"
    return None


# ---------------------------------------------------------------- rendering


def hook_command(zed_home):
    return str(Path(zed_home) / "bin" / "zed-deny")


def stop_command(zed_home):
    return f"{Path(zed_home) / 'bin' / 'zed-employee'} stop-guard"


def claude_settings(zed_home):
    """Contents of the per-session `claude --settings` file (additive to the project's own settings)."""
    return {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": hook_command(zed_home), "timeout": 10}]}],
                      "Stop": [{"hooks": [{"type": "command", "command": stop_command(zed_home), "timeout": 10}]}]}}


def copilot_plugin(directory, zed_home, name="zed-deny"):
    """A per-session Copilot plugin carrying the hook (PascalCase event names are required; see docs/runtimes/copilot.md)."""
    directory = Path(directory)
    (directory / "hooks").mkdir(parents=True, exist_ok=True)
    (directory / "plugin.json").write_text(json.dumps({"name": name, "version": "1.0.0", "description": "Zed hat deny policy"}, indent=2) + "\n")
    hooks = {"version": 1, "hooks": {"PreToolUse": [{"type": "command", "bash": hook_command(zed_home), "timeoutSec": 10}],
                                     "Stop": [{"type": "command", "bash": stop_command(zed_home), "timeoutSec": 10}]}}
    (directory / "hooks" / "hooks.json").write_text(json.dumps(hooks, indent=2) + "\n")
    return directory


def copilot_deny_tools(manifest):
    return [f"shell({prefix})" for prefix in manifest["deny"]["commands"]]
