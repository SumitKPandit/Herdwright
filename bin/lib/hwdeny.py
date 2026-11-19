"""A hat's `deny` policy (templates/hats/<hat>/capabilities.json). The decision lives here; the hook script
bin/hw-deny only feeds it a tool call. It runs as a PreToolUse command hook in a per-session settings file
(`claude --settings`) or from an OpenCode V2 plugin (`opencode_plugin`); git itself is guarded by bin/hw-guard, which spawn installs in the worktree.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from itertools import dropwhile, takewhile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hwgates  # noqa: E402
import hwshell  # noqa: E402
from hwshell import unresolved  # noqa: E402

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
CANARY_COMMAND = "hw-deny-canary"
PRETOOL_CANARY = "hw-pretool-canary"  # the coordinator's: bin/hw-pretool denies it in every session
# What a hat session's identity rests on (`hwemployee.caller_session`): a Bash command may not set or unset them
IDENTITY_VARS = ("HW_SESSION", "HW_HAT", "HW_PROFILE_DIR", "HERDR_PANE_ID")
ENV_SETTERS = hwshell.DECLARERS | {"env", "set"}
WRAPPERS = ("builtin", "command", "exec", "nohup", "time", "nice")
LOOK_THROUGH = set(hwshell.WRAPPERS) | {"find", "script"}  # run the command that follows
# The two git commands that make a repo for a hat that does not edit, in its scratch folder (`git_problem`):
# `git clone <from> <to>` and `git init [<folder>]`. No option here takes the next word or names a program
# (hwshell.ReadForm): `--depth=1`, not `--depth 1`.
GIT_MAKES = {"clone": hwshell.ReadForm({"-q", "--quiet", "-v", "--verbose", "--progress", "-l", "--local", "--no-local",
                                        "--no-hardlinks", "-n", "--no-checkout", "--bare", "--single-branch",
                                        "--no-single-branch", "--no-tags"}, {"--depth", "--branch", "--origin"}, positionals=2),
             "init": hwshell.ReadForm({"-q", "--quiet", "--bare"}, {"--initial-branch"}, positionals=1)}
SCRATCH = "its scratch folder (a folder under the temp directory, outside the worktree and the profile)"


def scratch_folder():
    """The scratch folder spawn handed this session (HW_SCRATCH), or None: a project without worktrees keeps it in
    the project folder (`T<id>-<agent>`), which is the session's worktree, so the guard has to be told which folder
    of it is the session's own."""
    given = os.environ.get("HW_SCRATCH")
    return Path(os.path.realpath(given)) if given else None


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
    other to the worktree. `{session}` in a pattern is this session's id (HW_SESSION)."""
    pattern = pattern.replace("{session}", os.environ.get("HW_SESSION", "-"))
    base, rel_pattern = (Path(profile), pattern[len("profile/"):]) if pattern.startswith("profile/") else (Path(worktree), pattern)
    base = Path(os.path.realpath(base))
    if hwshell.case_blind(base):  # the disk takes `WT` and `wt` for one folder: so does the guard
        p, base, rel_pattern = hwshell.one_case(p), hwshell.one_case(base), rel_pattern.lower()
    return (p == base or base in p.parents) and bool(glob_regex(rel_pattern).match(str(p.relative_to(base))))


def denied_path(path, patterns, worktree, profile, cwd, exceptions=()):
    """The pattern that denies `path`, or None. A pattern beginning `profile/` is relative to the profile folder,
    any other to the worktree; paths outside both (scratch, evidence directories, /tmp) are never denied, nor is a
    path in the scratch folder spawn handed the session (`scratch_folder`). A path matching an exception is allowed."""
    p = Path(os.path.expanduser(str(path)))
    p = Path(hwshell.first_name(os.path.realpath(p if p.is_absolute() else Path(cwd) / p)))
    if (own := scratch_folder()) and hwshell.inside(p, [own], "/"):
        return None
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
    if hwshell.one_case(p) == hwshell.one_case(profile) or hwshell.one_case(p) in hwshell.one_case(profile).parents:
        return True
    top = lambda d: subprocess.run(["git", "-C", str(d), "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    return p.is_dir() and top(p) != "" and os.path.realpath(top(p)) == os.path.realpath(top(profile))


def same_repo(place, worktree):
    """True when the folder `place` belongs to the repo the worktree belongs to (its main checkout, another worktree
    of it): a ref written there is written in the repo this hat does not change."""
    def common(folder):
        return subprocess.run(["git", "-C", str(folder), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                              capture_output=True, text=True).stdout.strip()
    return os.path.isdir(place) and common(place) != "" and os.path.realpath(common(place)) == os.path.realpath(common(worktree))


def leaves_the_clone(sub, args):
    """Why `git <sub> <args>` in a scratch clone writes outside it though it names no path there, or None: `config`
    on another file than the clone's own (the user's `~/.gitconfig`, the system's, one named by `--file`), and
    `apply --unsafe-paths`, which writes where the patch says."""
    if sub == "config" and (option := next((a for a in args if hwshell.abbreviates(a, ("--global", "--system", "--file"))
                                            or re.fullmatch(r"-[A-Za-z]*f.*", a)), None)):
        return (f"`{option}` sets configuration outside the clone's own (the user's git identity is theirs): write "
                f"`git -C <clone> config user.email <x>`")
    if sub == "apply" and (option := next((a for a in args if hwshell.abbreviates(a, ("--unsafe-paths",))), None)):
        return f"with `{option}` it writes where the patch says, outside the clone too"
    return None


def scratch_roots():
    """Where a hat that does not edit keeps its clones: the temp directories (a Claude Code session's scratchpad lies
    there), and the scratch folder spawn handed it (`scratch_folder`)."""
    return [Path(os.path.realpath(r)) for r in (tempfile.gettempdir(), "/tmp", "/private/tmp")] + list(filter(None, [scratch_folder()]))


def git_problem(words, cwd, worktree, profile, moved, made):
    """Reason to deny the `git ...` command `words` to a hat that does not edit, or None. Wherever it runs, git only
    reads: a known read (hwshell.GIT_READS, the allow list the coordinator's guard uses), each word written out. One
    opening, the hat's scratch folder (`scratch_roots`, outside the worktree, its repo and the checkout that holds the
    profile): `git clone` and `git init` whose target lies there, and any git command in a repo there, when the folder
    after `-C` and every path it names lie there too, it names no program for git to run, and it is no `push` and no
    `fetch` or `pull` with a refspec, which write another repo, and no option that reaches outside by itself
    (`leaves_the_clone`). The folder git runs in has to be there when the line is checked, or be made by a `git
    clone` or `git init` earlier on the line (`made`, which this function adds to): which repo a folder the line
    itself makes leads to, `same_repo` cannot ask. Where the guard cannot be sure in which directory the shell
    stands (`cwd` is None: hwshell.Standing) no git command runs."""
    sub, args, place = hwshell.git_command(words, cwd)
    sub_text = f"`git {sub}`" if sub else "`git`"
    if cwd is None:
        return (f"{sub_text} cannot be checked: it stands after a `cd` the guard does not follow, so it cannot tell where "
                f"git runs. It follows only `cd <folder that is there> && ...` at the head of the line, up to the first "
                f"`;`, `||` or new line: write `cd <folder> && git ...`, or run git as a command line of its own with "
                f"`git -C <folder>`")
    if expanded := next((w for w in words[1:] if unresolved(w)), None):
        return f"{sub_text} cannot be checked: the shell expands `{expanded}`, so the guard cannot tell what git is given. Write it out"
    read_problem = hwshell.git_read_problem(words)
    if read_problem is None:
        return None
    own = scratch_folder()
    only = (f"this hat does not change the repo, so its git only reads, each command in its strict form. The one opening "
            f"is {f'its scratch folder ({own})' if own else SCRATCH}: `git clone <worktree> <scratch folder>/<name>`, "
            f"then `git -C <scratch folder>/<name> ...` with every path inside it")
    names = [a for a in args if not a.startswith("-")]
    if problem := hwshell.git_global_problem(words)[0]:
        return f"{sub_text} is denied: it is no read ({read_problem}), and {problem}; {only}"
    if sub in GIT_MAKES:
        if problem := GIT_MAKES[sub].problem(args) or (sub == "clone" and len(names) != 2 and "write `git clone <from> <to>`"):
            return f"{sub_text} is denied: {problem}; {only}"
        paths = names[-1:] or [place]  # where the new repo goes; `git init` alone makes it where git stands
    elif sub == "push" or (sub in ("fetch", "pull") and len(names) > 1):
        return (f"{sub_text} is denied: {'it writes' if sub == 'push' else 'with a refspec it can write'} another repo "
                f"than the one it runs in, the one behind the worktree too; {only}")
    elif problem := hwshell.git_runs_a_program(sub, args):
        return f"{sub_text} is denied: {problem}, which the guard does not follow; {only}"
    elif problem := leaves_the_clone(sub, args):
        return f"{sub_text} is denied: {problem}; {only}"
    else:
        paths = [place, *hwshell.git_named_paths(args)]
    for path in paths:
        if hwshell.walks_through(path, place, moved):
            return (f"{sub_text} cannot be checked: {path} passes through a path an earlier command of this line moves, "
                    f"copies or links. Run it as a command line of its own")
        there = os.path.join(place, os.path.expanduser(path))
        if not hwshell.inside(there, scratch_roots(), "/") or denied_path(there, ["**", "profile/**"], worktree, profile, "/"):
            return f"{sub_text} is denied: it is no read ({read_problem}) and names {path}, outside the scratch folder; {only}"
    if sub in GIT_MAKES:
        made.add(os.path.realpath(os.path.join(place, os.path.expanduser(paths[0]))))
    elif holds_profile(place, profile, "/") or same_repo(place, worktree):
        return f"{sub_text} is denied: it is no read ({read_problem}) and runs in the worktree's repo or the profile's; {only}"
    elif not os.path.isdir(place) and not hwshell.inside(place, made, "/"):
        return (f"{sub_text} is denied: it is no read ({read_problem}) and {place} is not there yet, so the guard cannot "
                f"tell which repo git would find there when the line runs. Make the repo with `git clone` or `git init`, "
                f"or run this as a command line of its own; {only}")
    return None


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
    like Herdwright's) or asks `hw-employee finish` for `--as-hw` (a hat session never needs it), else None. Programs that run
    another (`sudo`, `xargs`, `find -exec`, ...) are looked through; `bash -c STRING` and `eval STRING` are parsed."""
    names = "/".join(IDENTITY_VARS)
    named = lambda w: any(w == v or w.startswith(v + "=") for v in IDENTITY_VARS)
    mentions = lambda w: any(v in w for v in IDENTITY_VARS)
    unnamed = lambda w: re.search(r"[$`{]", w.split("=", 1)[0])  # a variable name the shell expands: `$V=x`, `HW_{SESSION,HAT}` (the tokenizer splits it at the comma)
    assignment = lambda w: re.fullmatch(r"\w+=.*", w)
    flat = [x for w in words for x in w.split()]  # `env -S 'hw-employee finish --as-hw'` is one word
    if "finish" in flat and any(len(w) >= 3 and "--as-hw".startswith(w.split("=")[0]) for w in flat):  # argparse accepts `--as`
        return "`hw-employee finish --as-hw` is denied to a hat session: it finishes only itself"
    if any(named(w) for w in takewhile(assignment, words)):
        return f"setting {names} is denied: they tell Herdwright which session this is"
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
        return f"`{name}` on {names} (or on a name the shell expands) is denied: they tell Herdwright which session this is"
    if name == "env" and any(w in ("-", "--ignore-environment") or re.fullmatch(r"-[A-Za-z]*i[A-Za-z]*", w) for w in words[1:]):
        return "`env -i` is denied: it clears the variables that tell Herdwright which session this is"
    if name in LOOK_THROUGH or wrapped:  # a wrapper's own flags (`nice -n 5 env ...`, `time -p`, `exec -a x`) hide the command: scan for it
        for i, w in enumerate(words[1:], 1):
            if Path(w).name in ENV_SETTERS | hwshell.SHELLS | {"eval"} and (problem := identity_tampering(words[i:])):
                return problem
    if name in hwshell.SHELLS:
        flag = next((i for i, w in enumerate(words) if i and re.fullmatch(r"-\w*c\w*", w)), None)
        inner = words[flag + 1] if flag is not None and flag + 1 < len(words) else ""
    else:
        inner = " ".join(words[1:]) if name == "eval" else ""
    try:
        return next((p for sub, _ in hwshell.scoped_commands(hwshell.loose_tokens(inner)) if (p := identity_tampering(sub))), None)
    except ValueError:  # an unclosed quote: shell_problem decides what a line that cannot be read means for the hat
        return None


def shell_problem(cmd, manifest, worktree, profile, cwd):
    """Reason to deny the shell command `cmd` to the hat `manifest` describes, or None. A hat that may not edit is
    held to the grammar the guards read (hwshell.GRAMMAR) and refused the rest. A hat that edits writes shell the
    grammar refuses all day (loops, arrays, here-documents), so its line is read as far as it can be (loose_tokens),
    and one that cannot be read at all (an unclosed quote) passes."""
    read_only = "**" in manifest["deny"]["edit"]  # a hat that may not edit at all may not commit either
    try:
        cmds = hwshell.scoped_commands(hwshell.tokens(cmd) if read_only else hwshell.loose_tokens(cmd))
        return commands_problem(cmds, manifest, worktree, profile, cwd, read_only)
    except hwshell.Unparsed as e:
        return (f"this command line is denied to a hat that does not edit: it cannot be checked. Refused here: {e}. The "
                f"guard reads only plain command lines: {hwshell.GRAMMAR}. A longer check goes in a script file "
                f"outside the worktree") if read_only else None


def commands_problem(cmds, manifest, worktree, profile, cwd, read_only):
    """Reason to deny the simple commands `cmds` (hwshell.scoped_commands) to the hat, or None. A hat that does not
    edit (`read_only`) is read by the grammar: a `cd` is followed only where the guard can be sure of it
    (hwshell.Standing), a path through one the line itself moves or links cannot be checked (hwshell.changed_paths),
    git only reads outside the scratch folder (git_problem), a line that runs git sets no GIT_* variable, and one
    that sets HOME names no path by `~`."""
    deny = manifest["deny"]
    patterns, exceptions = deny["edit"], deny.get("except", [])
    standing = hwshell.Standing(cwd, strict=read_only)  # where each shell of the line stands: a `cd` in a subshell or a pipe does not move the others
    moved = set()  # read_only: the paths that may lead elsewhere after the commands read so far
    git_variable, runs_git = None, False  # read_only: a GIT_* variable the line sets; whether it runs git
    made = set()  # read_only: the folders a `git clone` or `git init` of this line makes a repo in
    sets_home = False  # read_only: the line set HOME, so the shell resolves `~` elsewhere than the guard does

    def write_problem(targets, cwd):
        for t in targets:
            if unresolved(t) or (cwd is None and not (Path(os.path.expanduser(t)).is_absolute())):
                return (f"writing {t} cannot be checked (the shell expands it, or the directory is unknown: a `cd` is "
                        f"followed only as `cd <folder that is there> && ...` at the head of the line): use a literal absolute path")
            if hwshell.walks_through(t, cwd, moved, at=False):
                return (f"writing {t} cannot be checked (it passes through a path an earlier command of this line "
                        f"changes): run it as a command line of its own")
            if pattern := denied_path(t, patterns, worktree, profile, cwd, exceptions):
                return f"writing {t} is denied to this hat ({pattern})"
        return None

    for words, scope in cmds:
        cwd, then = standing.at(scope), getattr(words, "then", None)
        if problem := identity_tampering(words):
            return problem
        if read_only:  # the grammar: redirections aside, wrappers by their exact options, `cd <path>` only
            own, before, redirects = hwshell.simple_command(words, piped=scope != ())
            words = before + own
            if problem := write_problem(redirects, cwd):
                return problem
            git_variable = git_variable or hwshell.guarded_variable(words, hwshell.NAMES_GIT)
            sets_home = sets_home or bool(hwshell.guarded_variable(words, hwshell.NAMES_HOME))
            if sets_home and (tilde := next((w for w in [*words, *redirects] if w.startswith("~")), None)):
                return (f"`{tilde}` cannot be checked: this command line sets `HOME`, so the shell resolves `~` elsewhere "
                        f"than the guard does. Write the path out")
        if any(re.fullmatch(r"CDPATH=.*", w) for w in takewhile(hwshell.ASSIGNMENT.fullmatch, words)):
            cwd = standing.dirs[scope] = None  # `CDPATH=x cd y` may land anywhere
        words = hwshell.command_words(words)  # a compound command's opener or a wrapper leaves the next word the command
        # a `cd` moves where later relative targets resolve; one the guard cannot follow (a hat that edits: `cd $X`,
        # `cd -`, `popd`) leaves the directory unknown: later relative targets cannot be checked
        standing.after(words, scope, then)
        if not words or Path(words[0]).name in hwshell.CD_COMMANDS:
            continue  # a `cd`, or only an opener (`do`, `then`) before the next `;`
        if prefix := command_denied(words, deny["commands"]):
            return f"`{prefix}` is denied to this hat: anything outward goes to Herdwright as a draft"
        if prefix := command_denied(words, deny.get("gated", [])):
            if reason := hwgates.gate_reason(worktree):
                return f"`{prefix}` waits for a clean review and qa of this branch: {reason}"
        if problem := write_problem(([] if read_only else hwshell.redirect_targets(words)) + hwshell.write_targets(words), cwd):
            return problem
        if not read_only:
            continue
        name = Path(words[0]).name
        if name == "git":
            runs_git = True
            if problem := git_problem(words, cwd, worktree, profile, moved, made):
                return problem
        if name == "find":  # what it runs is read like a command, on each path it starts from
            for _, run in hwshell.find_runs(words):
                for start in hwshell.find_starts(words):
                    ran = [hwshell.Word(w.replace("{}", start)) for w in run]
                    runs_git = runs_git or Path((hwshell.command_words(ran) or [""])[0]).name == "git"
                    if problem := commands_problem([(ran, scope)], manifest, worktree, profile, cwd, True):
                        return problem
        if git_variable and runs_git:
            return (f"this command line sets `{git_variable}` and runs git: git would follow the variable, into the "
                    f"worktree too, so the guard cannot tell where it acts. Use git's own options (`git -C <dir>`), or "
                    f"put the commands in a script file outside the worktree")
        moved |= hwshell.changed_paths(words, cwd)
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


def hook_command(hw_home):
    return str(Path(hw_home) / "bin" / "hw-deny")


def stop_command(hw_home):
    return f"{Path(hw_home) / 'bin' / 'hw-employee'} stop-guard"


def claude_settings(hw_home, deny_tools=()):
    """Contents of the per-session `claude --settings` file (additive to the project's own settings). `deny_tools` are the
    tool names a hat session may not call (`worker.deny_tools` in the profile's config/settings.json), e.g. a connector's
    write tools: only Herdwright, with the user present, posts."""
    return {"permissions": {"deny": list(deny_tools)},
            "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": hook_command(hw_home), "timeout": 10}]}],
                      "Stop": [{"hooks": [{"type": "command", "command": stop_command(hw_home), "timeout": 10}]}]}}


def opencode_plugin(directory, hw_home, env, deny_tools=()):
    """A per-session OpenCode config directory (the worker's server runs with OPENCODE_CONFIG_DIR pointing at it) whose
    V2 plugin runs bin/hw-deny before every tool call and throws its reason when the call is denied; a throw stops the
    call. `env` (HW_HAT, HW_SESSION, HW_WORKTREE, HW_PROFILE_DIR, HW_SCRATCH when the session has one) is written into
    the plugin, so the policy does not depend on how the server was started; `deny_tools` are tool names no worker may
    call (see `claude_settings`).
    The same check runs when the server starts a shell for any other caller (its `session.shell` and `/api/shell`
    routes pass no tool hook but do pass the shell hook), and the server's password is taken out of every shell's
    environment. Not stopped, and said so in the README: a worker that reads the password from the process list and
    uses the server's terminal route (`/api/pty` passes no hook), starts a server of its own, or runs a script.
    A shell tool call passes both hooks, so hw-deny runs twice for it: the second run is what checks the routes.
    A hw-deny that cannot run denies. OpenCode has no hook that can refuse a turn end, so there is no stop-guard here;
    a finish that was skipped still shows up as `done:` without a recorded finish. V1 is not supported: a V1 server
    ignores this file, and spawn's canary then refuses the worker."""
    plugin = Path(directory) / "plugins" / "hw-deny" / "index.js"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text(f"""import {{ Plugin }} from "@opencode/plugin";
import {{ spawnSync }} from "node:child_process";

const ENV = {json.dumps(dict(env))};
const DENY_TOOLS = {json.dumps(list(deny_tools))};

export default Plugin.define({{
  id: "hw.deny",
  async setup(ctx) {{
    const check = (tool, input) => {{
      const res = spawnSync({json.dumps(hook_command(hw_home))}, [], {{
        input: JSON.stringify({{ tool_name: tool, tool_input: input, cwd: ctx.location.directory }}),
        encoding: "utf-8",
        env: {{ ...process.env, ...ENV }},
      }});
      if (res.status !== 0) throw new Error((res.stderr || res.stdout || "hw-deny could not run").trim());
    }};
    await ctx.tool.hook("execute.before", (event) => {{
      if (DENY_TOOLS.includes(event.tool)) throw new Error(`hw-deny: the tool ${{event.tool}} is denied to a worker`);
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


def coordinator_plugin(directory, hw_home, env):
    """The coordinator's OpenCode config directory gets a V2 plugin that runs bin/hw-pretool before every tool call,
    with the call's session id, and throws its reason when the call is denied. hw-pretool acts only on the session
    holding the profile lock (and on its canary), so the plugin is inert anywhere else. `env` (HW_PROFILE_DIR) is
    written into the plugin. A hw-pretool that cannot run denies: `bin/hw coordinator` proved it ran once (the
    canary), so a later failure is a broken install, not a call to wave through."""
    plugin = Path(directory) / "plugins" / "hw-pretool" / "index.js"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text(f"""import {{ Plugin }} from "@opencode/plugin";
import {{ spawnSync }} from "node:child_process";

const ENV = {json.dumps(dict(env))};

export default Plugin.define({{
  id: "hw.pretool",
  async setup(ctx) {{
    await ctx.tool.hook("execute.before", (event) => {{
      const res = spawnSync({json.dumps(str(Path(hw_home) / "bin" / "hw-pretool"))}, [], {{
        input: JSON.stringify({{ tool_name: event.tool, tool_input: event.input, cwd: ctx.location.directory, session_id: event.sessionID }}),
        encoding: "utf-8",
        env: {{ ...process.env, ...ENV }},
      }});
      if (res.status !== 0) throw new Error((res.stderr || "hw-pretool could not run").trim());
      const decision = res.stdout.trim() ? JSON.parse(res.stdout).hookSpecificOutput : null;
      if (decision && decision.permissionDecision === "deny") throw new Error(decision.permissionDecisionReason);
    }});
  }},
}});
""")
    return Path(directory)
