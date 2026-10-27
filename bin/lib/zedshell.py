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
    return list(lex)


def split_commands(toks):
    """[(words, depth_zero)] — simple commands with redirections kept as words; depth_zero False inside ( ... )."""
    cmds, cur, depth = [], [], 0
    for t in toks:
        if t in OPERATORS:
            if cur:
                cmds.append((cur, depth == 0))
            cur = []
            depth += 1 if t == "(" else -1 if t == ")" else 0
        else:
            cur.append(t)
    if cur:
        cmds.append((cur, depth == 0))
    return cmds


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
