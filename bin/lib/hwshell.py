"""Shell-command parsing shared by the hook scripts (hw-pretool, hw-deny): split a Bash command into simple
commands, find the git subcommand and its repo, and test whether a path lies inside a set of roots."""
import os
import re
import shlex
from itertools import dropwhile, takewhile
from pathlib import Path

GIT_WRITE = {"commit", "push", "rebase", "merge", "cherry-pick", "reset", "revert", "add", "apply", "am", "tag", "stash", "rm", "mv", "restore",
             "clean", "checkout", "switch"}
FILE_WRITERS = {"rm", "mv", "cp", "touch", "mkdir", "tee", "ln", "rmdir", "truncate", "chmod", "chown", "install", "rsync"}
PIPES = {"|", "|&"}
OPERATORS = {"&&", "||", ";", "&", "(", ")"} | PIPES
REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&"}
# Every operator a run of punctuation can hold, longest first: `;>` is `;` then `>`
SYMBOLS = sorted(OPERATORS | REDIRECTS | {";;", "<", "<<", "<<<", "<&", "<>"}, key=len, reverse=True)
# What may stand before a simple command's program: the opener of a compound command, and programs that run the
# command that follows them, each with its options that take a value
OPENERS = {"{", "then", "do", "else", "elif", "if", "while", "until", "!"}
WRAPPERS = {"builtin": (), "command": (), "exec": ("-a",), "nohup": (), "time": (), "nice": ("-n",), "env": ("-u", "-C"),
            "sudo": ("-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U"), "doas": ("-u", "-C"),
            "xargs": ("-n", "-I", "-P", "-L", "-s", "-d", "-E", "-a"), "timeout": ("-s", "-k"), "gtimeout": ("-s", "-k"),
            "stdbuf": ("-i", "-o", "-e"), "setsid": (), "caffeinate": ("-t", "-w")}
WRAPPER_ARGUMENTS = {"timeout": 1, "gtimeout": 1}  # words of its own a wrapper takes before the command: the duration
ASSIGNMENT = re.compile(r"\w+=.*")
FIND_OPTIONS = {"-H", "-L", "-P", "-D", "-E", "-X", "-d", "-s", "-x", "-f"}  # GNU's and BSD's, before the paths; -D and -f take a word
MODE = re.compile(r"[0-7]{3,4}|[ugoa]*[-+=][rwxXstugo]*(,[ugoa]*[-+=][rwxXstugo]*)*")  # chmod's first argument


def inside(path, roots, cwd):
    p = Path(os.path.expanduser(str(path)))
    p = p if p.is_absolute() else Path(cwd) / p
    p = Path(os.path.realpath(p))
    return any(p == r or r in p.parents for r in roots)


HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?([^\n]*)\n.*?\n\s*\1\s*(?=\n|$)", re.S)


def strip_heredocs(cmd):
    """`cmd` without its here-documents' bodies. The rest of the line that opens one stays: `cat <<EOF > file` writes
    `file`. A line may open several."""
    while True:
        cmd, found = HEREDOC.subn(lambda m: f" {m.group(2)}\n", cmd, count=1)
        if not found:
            return cmd


def tokens(cmd):
    lex = shlex.shlex(strip_heredocs(cmd).replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = False
    lex.wordchars += "${}+:@%"  # keep `$E` / `${E}` one word, so `git -C $E commit` reads as a git commit in an unknown repo
    # shlex returns a run of punctuation as one word (`);`, `))`, `;>`): it is cut into its operators
    return [part for t in lex for part in (operators(t) if re.fullmatch(r"[();<>|&]+", t) else [t])]


def operators(run):
    """The operators a run of punctuation holds, the longest match first: `;>` is `;` and `>`, `&>>` is itself."""
    out = []
    while run:
        out.append(next((s for s in SYMBOLS if run.startswith(s)), run[0]))
        run = run[len(out[-1]):]
    return out


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
            flush(piped or t in PIPES or t == "&")
            piped = t in PIPES
            if t == "(":
                count += 1
                stack.append(stack[-1] + (count,))
            elif t == ")" and len(stack) > 1:
                stack.pop()
        else:
            cur.append(t)
    flush(piped)
    return cmds


def command_words(words):
    """A simple command's own words, the program first: without the variable assignments, the compound command's
    opener (`{`, `then`, `do`, `!` ...) and the wrappers (`command`, `env`, `sudo`, `xargs`, `timeout` ...) before it.
    So `if true; then FOO=1 env touch x; fi` reads as `touch x`. [] when nothing is left (`do`, `fi`, `}` alone)."""
    while words:
        name = Path(words[0]).name
        if ASSIGNMENT.fullmatch(words[0]) or words[0] in OPENERS:
            words = words[1:]
        elif name in WRAPPERS:
            words = words[1:]
            while words and (words[0].startswith("-") or (name == "env" and ASSIGNMENT.fullmatch(words[0]))):
                words = words[1 + (words[0] in WRAPPERS[name]):]
            words = words[WRAPPER_ARGUMENTS.get(name, 0):]
        else:
            break
    return words


def redirect_targets(words):
    """The files the redirections among `words` write: the word after `>`, `>>`, `>|`, `&>`, `&>>` or `>&`, unless
    that names a file descriptor (`>&2`, `2>&1`, `>&-`)."""
    return [t for w, t in zip(words, words[1:]) if w in REDIRECTS and not (w == ">&" and re.fullmatch(r"\d+|-", t))]


def write_targets(words):
    """The paths the simple command `words` (its own words: `command_words`) writes by what it is, redirections
    aside: the arguments of a file-writing program (rm, cp, touch, chmod ... and sed/perl in place), `dd of=`, and the
    start paths of a `find` that deletes or runs such a program."""
    if not words:
        return []
    name, args = Path(words[0]).name, [w for w in words[1:] if not w.startswith("-")]
    if name == "chmod":
        return list(dropwhile(lambda w: MODE.fullmatch(w) or w == ",", args))  # the tokenizer cuts a mode at its commas
    if name == "chown":
        return args[1:]
    if name in FILE_WRITERS or (name in ("sed", "perl") and any(re.fullmatch(r"-\w*i\w*", w) or w.startswith("-i") for w in words[1:])):
        return args
    if name == "dd":
        return [w[3:] for w in words[1:] if w.startswith("of=")]
    if name == "find":
        runs = [words[i + 1:] for i, w in enumerate(words) if w in ("-exec", "-execdir", "-ok", "-okdir")]
        if "-delete" in words or any(write_targets(command_words(run)) for run in runs):
            rest, named = words[1:], []
            while rest and (rest[0] in FIND_OPTIONS or rest[0].startswith("-O")):  # its options stand before the paths
                named += rest[1:2] if rest[0] == "-f" else []
                rest = rest[1 + (rest[0] in ("-D", "-f")):]
            return named + list(takewhile(lambda w: not w.startswith(("-", "(", "!")), rest)) or ["."]
    return []


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
    known (a target the shell expands, `cd -`, `popd`, a bare `pushd`, which swaps the two directories on top of the
    stack, an unknown `cwd` with a relative target). A bare `cd` leads home."""
    args = [w for w in words[1:] if w != "--" and not re.fullmatch(r"-[LPe@]+", w)]
    if args and not unresolved(args[0]) and args[0] != "-":
        d = Path(os.path.expanduser(args[0]))
        if d.is_absolute() or cwd is not None:
            return os.path.realpath(d if d.is_absolute() else Path(cwd) / d)
        return None
    return os.path.realpath(os.path.expanduser("~")) if not args and Path(words[0]).name == "cd" else None


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
