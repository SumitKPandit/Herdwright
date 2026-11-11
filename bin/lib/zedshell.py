"""Shell-command parsing shared by the hook scripts (zed-pretool, zed-deny): split a Bash command into simple
commands, find the git subcommand and its repo, and test whether a path lies inside a set of roots."""
import os
import re
import shlex
from pathlib import Path

GIT_WRITE = {"commit", "push", "rebase", "merge", "cherry-pick", "reset", "revert", "add", "apply", "am", "tag", "stash", "rm", "mv", "restore"}
FILE_WRITERS = {"rm", "mv", "cp", "touch", "mkdir", "tee", "ln", "rmdir", "truncate"}
OPERATORS = {"&&", "||", ";", "|", "&", "(", ")"}
REDIRECTS = {">", ">>", ">|"}


def inside(path, roots, cwd):
    p = Path(os.path.expanduser(str(path)))
    p = p if p.is_absolute() else Path(cwd) / p
    p = Path(os.path.realpath(p))
    return any(p == r or r in p.parents for r in roots)


def strip_heredocs(cmd):
    return re.sub(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1\s*(?=\n|$)", "\n", cmd, flags=re.S)


def tokens(cmd):
    lex = shlex.shlex(strip_heredocs(cmd).replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = False
    lex.wordchars += "${}"  # keep `$E` / `${E}` one word, so `git -C $E commit` reads as a git commit in an unknown repo
    # shlex returns a run of punctuation as one word (`);`, `))`): a parenthesis is an operator of its own
    return [part for t in lex for part in (re.findall(r"[()]|[^()]+", t) if re.fullmatch(r"[();<>|&]+", t) else [t])]


def scoped_commands(toks):
    """[(words, scope)] — simple commands with redirections kept as words. `scope` names the shell a command runs in:
    () for the command line's own shell, a longer tuple inside ( ... ), on either side of a pipe and for a command
    sent to the background. A `cd` changes the directory of its own scope and of the scopes that begin after it."""
    cmds, cur, stack, count, piped = [], [], [()], 0, False

    def flush(own):
        nonlocal count, cur
        if cur:
            count += own
            cmds.append((cur, stack[-1] + ((count,) if own else ())))
        cur = []

    for t in toks:
        if t in OPERATORS:
            flush(piped or t in ("|", "&"))
            piped = t == "|"
            if t == "(":
                count += 1
                stack.append(stack[-1] + (count,))
            elif t == ")" and len(stack) > 1:
                stack.pop()
        else:
            cur.append(t)
    flush(piped)
    return cmds


def split_commands(toks):
    """[(words, depth_zero)] — as scoped_commands; depth_zero is True for a command in the command line's own shell."""
    return [(words, scope == ()) for words, scope in scoped_commands(toks)]


def scope_dir(dirs, scope):
    """The directory the shell `scope` stands in (None: unknown), from `dirs` ({scope: directory}, filled as scopes
    are met): a new scope begins where its parent stands at that moment."""
    if scope not in dirs:
        dirs[scope] = scope_dir(dirs, scope[:-1])
    return dirs[scope]


UNRESOLVED = re.compile(r"[$`]|^~[^/]")
CD_COMMANDS = ("cd", "pushd", "popd")


def unresolved(path):
    """True when the shell would expand `path` (variable, command substitution, `~user`), which a hook cannot follow."""
    return bool(UNRESOLVED.search(str(path)))


def cd_target(words, cwd):
    """Where the `cd`, `pushd` or `popd` command `words` leaves a shell that stood in `cwd`; None when that cannot be
    known (a target the shell expands, `cd -`, `popd`, a bare `cd`, an unknown `cwd` with a relative target)."""
    args = [w for w in words[1:] if w != "--" and not re.fullmatch(r"-[LPe@]+", w)]
    if args and not unresolved(args[0]) and args[0] != "-":
        d = Path(os.path.expanduser(args[0]))
        if d.is_absolute() or cwd is not None:
            return os.path.realpath(d if d.is_absolute() else Path(cwd) / d)
        return None
    return cwd if not args and Path(words[0]).name == "pushd" else None


def git_target(words, cwd):
    """(subcommand, repo dir) of a `git ...` command, skipping global options."""
    i, target = 1, cwd
    while i < len(words):
        w = words[i]
        if w == "-C" and i + 1 < len(words):
            if os.path.isabs(os.path.expanduser(words[i + 1])):
                target = words[i + 1]
            else:
                target = None if target is None else str(Path(target) / words[i + 1])
            i += 2
        elif w in ("-c", "--git-dir", "--work-tree") and i + 1 < len(words):
            i += 2
        elif w.startswith("-"):
            i += 1
        else:
            return w, target
    return None, target
