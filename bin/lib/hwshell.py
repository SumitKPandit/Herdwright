"""Shell-command parsing shared by the hook scripts (hw-pretool, hw-deny): read a command line by the small grammar
the guards accept (`tokens`, GRAMMAR) and refuse the rest, split it into simple commands, find the git subcommand and
its repo, and test whether a path lies inside a set of roots. A hat that edits is not held to the grammar: its
command lines are read by `loose_tokens`."""
import functools
import os
import re
from itertools import dropwhile, takewhile
from pathlib import Path

FILE_WRITERS = {"rm", "mv", "cp", "touch", "mkdir", "tee", "ln", "rmdir", "truncate", "chmod", "chown", "install", "rsync",
                "unlink", "mkfifo", "mknod", "shred", "ditto", "chgrp", "chflags", "mktemp"}
FIND_RUNS = ("-exec", "-execdir", "-ok", "-okdir")
FIND_FILES = ("-fprint", "-fprint0", "-fls", "-fprintf")  # GNU find: the next word is a file it writes
PIPES = {"|", "|&"}
OPERATORS = {"&&", "||", ";", "&", "(", ")"} | PIPES
REDIRECTS = {">", ">>", ">|", "&>", "&>>", ">&", "<>"}  # `<>` creates the file it opens
# Every operator a run of punctuation can hold, longest first: `;>` is `;` then `>`
CASE_ENDS = {";;", ";&", ";;&"}  # they end a command like `;`
SYMBOLS = sorted(OPERATORS | REDIRECTS | CASE_ENDS | {"<", "<<<", "<&"}, key=len, reverse=True)
# What may stand before a simple command's program: the opener of a compound command (loose reading only), and
# programs that run the command that follows them, each with its options that take a value
OPENERS = {"{", "then", "do", "else", "elif", "if", "while", "until", "!"}
WRAPPERS = {"builtin": (), "command": (), "exec": ("-a",), "nohup": (), "time": (), "nice": ("-n",), "env": ("-u", "-C", "-P"),
            "noglob": (), "nocorrect": (), "coproc": (), "repeat": (), "arch": ("-arch", "-e", "-d"), "busybox": (), "unbuffer": (),
            "ionice": ("-c", "-n", "-p"), "watch": ("-n",),
            "sudo": ("-u", "-g", "-h", "-p", "-C", "-D", "-R", "-T", "-U"), "doas": ("-u", "-C"),
            "xargs": ("-n", "-I", "-P", "-L", "-s", "-d", "-E", "-a", "-J", "-R", "-S"), "timeout": ("-s", "-k"), "gtimeout": ("-s", "-k"),
            "stdbuf": ("-i", "-o", "-e"), "setsid": (), "caffeinate": ("-t", "-w"),
            "xcrun": ("--sdk", "-sdk", "--toolchain", "-toolchain"),
            "taskpolicy": ("-d", "-g", "-c", "-t", "-l", "-S", "-m", "-j"), "lockf": ("-t",), "chronic": (),
            "flock": ("-w", "--timeout", "-E", "--conflict-exit-code"), "sandbox-exec": ("-f", "-n", "-p", "-D")}
# words of its own a wrapper takes before the command: the duration, the count, the lock file
WRAPPER_ARGUMENTS = {"timeout": 1, "gtimeout": 1, "repeat": 1, "lockf": 1, "flock": 1}
WRAPPER_FILES = {"lockf", "flock"}  # ... which these create: it is checked as a file they write
# a wrapper's options that move the command to another directory or root: never taken under the grammar
WRAPPER_MOVES = {"env": ("-C",), "sudo": ("-D", "-R"), "doas": ("-C",)}
# Under the grammar a wrapper takes an option only from an exact set, as git does (GIT_READS): the options above that
# take the next word, `--`, and these that take none or hold their value. An option nobody listed could take the
# next word as its value and so hide the program (`env -P <dir> touch x`), so it is refused. A wrapper's option that
# changes directory or runs a string (`env -C`, `env -S`, `sudo -D`) is in no set.
WRAPPER_FLAGS = {"command": r"-[pvV]+", "exec": r"-[cl]+", "time": r"-p", "nice": r"-\d+|--adjustment=-?\d+",
                 "env": r"-i|-|--ignore-environment|-u\w+|--unset=\w+", "sudo": r"-[nEH]+", "doas": r"-n",
                 "xargs": r"-[0tprxo]+|-[nIPLsJRS][^-]\S*", "timeout": r"--preserve-status|--foreground|-v|--verbose|--signal=\w+|--kill-after=[\w.]+",
                 "gtimeout": r"--preserve-status|--foreground|-v|--verbose|--signal=\w+|--kill-after=[\w.]+",
                 "stdbuf": r"-[ioe](L|\d+[KMG]?)|--(input|output|error)=\w+", "caffeinate": r"-[dimsu]+",
                 "arch": r"-(arm64e?|x86_64h?|i386|32|64|c)", "ionice": r"-t|-[cnp]\d+", "watch": r"-[dt]+|-n[\d.]+",
                 "unbuffer": r"-p", "xcrun": r"--run", "setsid": r"-[wf]+", "taskpolicy": r"-[bBas]+", "lockf": r"-[knsw]+",
                 "chronic": r"-[ev]+",
                 "flock": r"-[sxeunoF]+|--(shared|exclusive|unlock|nonblock|nb|close|no-fork|verbose)|--timeout=[\d.]+|--conflict-exit-code=\d+"}
ASSIGNMENT = re.compile(r"\w+\+?=.*", re.S)
# A word of options before find's paths, GNU's and BSD's: flags alone or grouped (`-EX`, BSD), `-O<level>` (GNU), `--`,
# and a group may end in -D <debug> (GNU) or -f <path> (BSD), which take the rest of the word or the next one.
FIND_OPTIONS = re.compile(r"-(?:[HLPEXdsx]+|(?P<flags>[HLPEXdsx]*)(?P<takes>[Df])(?P<attached>.*)|O\d*|-)")
MODE = re.compile(r"[0-7]{3,4}|[ugoa]*[-+=][rwxXstugo]*(,[ugoa]*[-+=][rwxXstugo]*)*")  # chmod's first argument


@functools.lru_cache(maxsize=None)
def _one_name(folder):
    other = folder.swapcase()
    return other != folder and os.path.exists(other) and os.path.samefile(folder, other)


def case_blind(path):
    """Whether the disk under `path` takes `WT` and `wt` for one name (a default macOS disk does): asked of the
    nearest folder of the path that is there."""
    there = str(path)
    while not os.path.lexists(there) and there != os.path.dirname(there):
        there = os.path.dirname(there)
    return _one_name(there)


def one_case(path):
    """The resolved `path` as a guard compares it: in lower case where its disk does not tell the cases apart."""
    return Path(str(path).lower() if case_blind(path) else str(path))


DATA_VOLUME = "/System/Volumes/Data"


def first_name(path):
    """The resolved `path` by its first name. macOS shows the data volume a second time under /System/Volumes/Data,
    which is no link, so `realpath` leaves it: that beginning is dropped when both names lead to one folder, asked of
    the nearest folder of the path that is there."""
    there = str(path)
    if not there.startswith(DATA_VOLUME + "/"):
        return there
    while not os.path.lexists(there) and there != DATA_VOLUME:
        there = os.path.dirname(there)
    short = there[len(DATA_VOLUME):]
    return str(path)[len(DATA_VOLUME):] if short and os.path.exists(short) and os.path.samefile(there, short) else str(path)


def inside(path, roots, cwd):
    p = Path(os.path.expanduser(str(path)))
    p = p if p.is_absolute() else Path(cwd) / p
    p = one_case(os.path.realpath(p))
    return any(p == r or r in p.parents for r in map(one_case, roots))


class Unparsed(ValueError):
    """A command line outside what a guard reads; the message names what it met and how to write it instead."""


class Word(str):
    """One word of a command line as the shell hands it to the program (quotes removed). `unknown`: it holds text only
    the shell knows (`$NAME`; in the loose reading any expansion); `glob`: an unquoted `*`, `?` or `[`; `extended`: an
    unquoted `^`, `#` or inner `~`, a pattern where zsh's extended_glob is on; `subs` (loose reading): the tokens of
    each `$( ... )` inside it. An operator is a plain str, so a quoted `";"` or `">"` is never taken for one."""
    unknown, glob, extended, subs = False, False, False, ()

    def __new__(cls, text, unknown=False, glob=False, subs=(), extended=False):
        self = super().__new__(cls, text)
        self.unknown, self.glob, self.extended, self.subs = unknown, glob, extended, tuple(subs)
        return self


# The whole of what the coordinator's guard and a read-only hat's guard read (`tokens`, `simple_command`). It is an
# allow list: the guards do not try to understand the shell, so a spelling nobody thought of is refused, not passed.
GRAMMAR = ("plain words; text in '...' or \"...\"; `$NAME` and `${NAME}`, but never in a program's name, a path that "
           "is written or a git argument; commands joined by `|`, `&&`, `||`, `;` or a new line; the redirects `>`, "
           "`>>`, `<`, `2>`, `2>>` to a path written out, and `2>&1`, `>&2`; `cd <path written out>` as a command of "
           "its own; a backslash before a character or at the end of a line")
REFUSED = ("a here-document or here-string, `$( )`, backquotes, `<( )`, `( )`, `{ }`, `(( ))`, `$(( ))`, `${NAME...}` "
           "beyond the plain form, `&`, a comment, a brace expansion, `if`/`for`/`while`/`case`/`function`, `eval`, "
           "`source`, a shell given `-c` or fed its input, and any character it does not know outside quotes")
_EXPANSION = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\}|\?)")
_BARE = frozenset("_-+.,/:@%=~^#{}[]*?!")  # besides ASCII letters and digits, what a word may hold outside quotes
_DESCRIPTOR = re.compile(r"&[0-2](?=[\s;&|<>]|$)")
_BRACES = re.compile(r"\{[^{}]*(?:,|\.\.)[^{}]*\}")
_HOW = {"<<": "a here-document or here-string (write the text in quotes, or put it in a file and name the file)",
        "$(": "`$( )` (run that command first and write its output out)",
        "`": "a backquoted command (run that command first and write its output out)",
        "#": "a comment (leave it out)"}


def tokens(cmd):
    """The words (Word) and operators (str: `;` `&&` `||` `|` `>` `>>` `<`) of a command line that keeps to GRAMMAR;
    anything else raises Unparsed, which a guard answers by refusing the line. A new line reads as `;`, a
    backslash-newline joins two lines, a redirect's file descriptor (`2>`) and `2>&1` leave no token, and the word
    after a redirect is its file, written out."""
    out, chars, unknown, awaiting = [], None, False, None  # chars: the word being read, [(char, quoted)]; awaiting: a redirect without its file yet
    i, n = 0, len(cmd)

    def refuse(what):
        raise Unparsed(what)

    def add(text, quoted):
        nonlocal chars
        chars = chars if chars is not None else []
        chars.extend((c, quoted) for c in text)

    def end_word():
        nonlocal chars, unknown, awaiting
        if chars is None:
            return
        text, bare = "".join(c for c, _ in chars), "".join("\0" if q else c for c, q in chars)
        if _BRACES.search(bare):
            refuse(f"the brace expansion in `{text}` (write each word out, or quote it)")
        if bare.startswith("=") and len(bare) > 1:
            refuse(f"`{text}`: zsh reads a word that begins with `=` as a program's path (quote it)")
        if bare.startswith("~") and bare != "~" and not bare.startswith("~/"):
            refuse(f"`{text}`: only `~` and `~/...` are read (write the path out)")
        word = Word(text, unknown or bool(re.search(r"[=:]~", bare)), glob=bool(re.search(r"[*?]|\[.|.\[", bare)),
                    extended="^" in bare or "#" in bare or "~" in bare[1:])
        if awaiting and (word.unknown or word.glob or word.extended):
            refuse(f"the redirect `{awaiting} {text}`: its file must be written out, with no `$NAME` and no pattern")
        out.append(word)
        chars, unknown, awaiting = None, False, None

    def end_command():
        end_word()
        if awaiting:
            refuse(f"a `{awaiting}` without its file")

    def dollar(quoted):
        nonlocal i, unknown
        if m := _EXPANSION.match(cmd, i):
            if cmd[m.end():m.end() + 1] == "[" and not m.group().endswith("}"):
                refuse(f"`{m.group()}[`: zsh reads a subscript there (write `${{NAME}}` or quote the `[`)")
            add(m.group(), True)
            i, unknown = m.end(), True
        elif cmd[i + 1:i + 2] in ("", " ", "\t", "\n") or (quoted and cmd[i + 1] == '"'):
            add("$", True)  # a `$` with nothing after it is itself
            i += 1
        else:
            refuse(_HOW["$("] if cmd.startswith("$(", i) and not cmd.startswith("$((", i) else
                   f"`{cmd[i:i + 2]}...`: of the shell's expansions only `$NAME` and `${{NAME}}` are read (write a literal `$` as `\\$` or in single quotes)")

    def redirect():
        nonlocal i, chars, awaiting
        if chars is not None:  # glued to the operator: its file descriptor, `2>`
            fd = "".join(c for c, q in chars if not q)
            if awaiting or len(fd) != len(chars) or fd not in ("0", "1", "2"):
                refuse(f"a `{cmd[i]}` glued to a word (put a space before a redirect; of the file descriptors only 0, 1 and 2 are read)")
            chars = None
        if cmd[i] == "<":
            if cmd[i + 1:i + 2] in ("<", "(", "&", ">"):
                refuse(_HOW["<<"] if cmd[i + 1] == "<" else f"`{cmd[i:i + 2]}` (of the input redirects only `< file` is read)")
            op, i = "<", i + 1
        else:
            op = ">>" if cmd.startswith(">>", i) else ">"
            i += len(op)
            if op == ">" and (m := _DESCRIPTOR.match(cmd, i)):  # `2>&1`, `>&2`: no file
                i = m.end()
                return
            if cmd[i:i + 1] in ("&", "|", "!", "(", "<", ">"):
                refuse(f"`{op}{cmd[i]}` (of the output redirects only `>`, `>>`, `2>`, `2>>` to a file and `2>&1`, `>&2` are read)")
        end_word()
        if awaiting:
            refuse(f"a `{awaiting}` without its file")
        out.append(op)
        awaiting = op

    while i < n:
        c = cmd[i]
        if c == "\\":
            if i + 1 >= n:
                refuse("a backslash at the end of the command line")
            if cmd[i + 1] != "\n":  # a backslash-newline joins the lines
                add(cmd[i + 1], True)
            i += 2
        elif c == "'":
            end = cmd.find("'", i + 1)
            if end < 0:
                refuse("an unclosed single quote")
            add(cmd[i + 1:end], True)
            i = end + 1
        elif c == '"':
            add("", True)
            i += 1
            while cmd[i:i + 1] != '"':
                if i >= n:
                    refuse("an unclosed double quote")
                if cmd[i] == "\\" and cmd[i + 1:i + 2] in ("$", "`", '"', "\\", "\n"):
                    add(cmd[i + 1].replace("\n", ""), True)
                    i += 2
                elif cmd[i] == "$":
                    dollar(True)
                elif cmd[i] == "`":
                    refuse(_HOW["`"])
                else:
                    add(cmd[i], True)
                    i += 1
            i += 1
        elif c == "$":
            dollar(False)
        elif c in " \t":
            end_word()
            i += 1
        elif c == "\n":
            end_command()
            out.append(";")
            i += 1
        elif c in "<>":
            redirect()
        elif c in ";&|":
            end_command()
            op = cmd[i:i + 2] if cmd[i:i + 2] in ("&&", "||") else c
            if op == "&" or (op in (";", "|") and cmd[i + 1:i + 2] in (";", "&", "|")):
                refuse(f"`{cmd[i:i + 2].strip()}` (commands are joined only by `|`, `&&`, `||`, `;` or a new line; a command "
                       f"is not sent to the background)")
            out.append(op)
            i += len(op)
        elif c in "`()" or (c == "#" and chars is None):
            refuse(_HOW.get(c, f"`{c}` outside quotes (a subshell, a function, an array, a glob qualifier)"))
        elif c.isascii() and (c.isalnum() or c in _BARE):
            add(c, False)
            i += 1
        else:
            refuse(f"the character {c!r} outside quotes (quote it)")
    end_command()
    return out


def loose_tokens(cmd):
    """The words (Word) and operators (str) of any command line, read as far as it can be, for a hat that edits: a
    backslash-newline joins two lines, a newline ends a command, a `#` at the start of a word begins a comment, a
    here-document's body is skipped, `a{b,c}` is two words, `$( ... )` is read as commands of its own (Word.subs),
    and a backquoted command, `<( )`, `$'...'` and an array are taken as plain text. Only an unclosed quote, `$(` or
    `${` raises Unparsed."""
    return _lex(cmd, 0, False)[0]


def _closing(s, i, opener, closer):
    """The index after the `closer` that matches the `opener` at s[i], or None."""
    depth = 0
    for j in range(i, len(s)):
        depth += (s[j] == opener) - (s[j] == closer)
        if depth == 0:
            return j + 1
    return None


def _braces(chars):
    """The words brace expansion makes of `chars` ([(char, quoted)]): `a{b,c}` is `ab` and `ac`."""
    depth, start, commas = 0, 0, []
    for k, (c, quoted) in enumerate(chars):
        if quoted:
            continue
        if c == "{":
            if depth == 0:
                start, commas = k, []
            depth += 1
        elif c == "," and depth == 1:
            commas.append(k)
        elif c == "}" and depth:
            depth -= 1
            if depth == 0 and commas:
                cuts = [start, *commas, k]
                return [w for a, b in zip(cuts, cuts[1:]) for w in _braces(chars[:start] + chars[a + 1:b] + chars[k + 1:])]
    return [chars]


def _lex(s, i, closing):
    """(tokens, index after them) of s from i: to the end, or with `closing` to the `)` that ends a `$(`."""
    n, out, depth = len(s), [], 0
    chars = None  # the word being read: [(char, quoted)]; None between words
    unknown, subs = False, []
    heredoc, pending = None, []  # a `<<` waiting for its delimiter (True: `<<-`); the here-documents of this line

    def add(text, quoted):
        nonlocal chars
        chars = chars if chars is not None else []
        chars.extend((c, quoted) for c in text)

    def end_word():
        nonlocal chars, unknown, subs, heredoc
        if chars is None:
            return
        if heredoc is not None:
            pending.append(("".join(c for c, _ in chars), heredoc))
            heredoc = None
        else:
            made = _braces(chars)
            for w in made if len(made) <= 256 else [chars]:
                text, bare = "".join(c for c, _ in w), "".join("\0" if q else c for c, q in w)
                out.append(Word(text, unknown or bool(re.search(r"\{[^{},]*\.\.[^{},]*\}|^~[^/]", bare)) or len(made) > 256,
                                len(bare) > 1 and any(c in bare for c in "*?["), subs))
        chars, unknown, subs = None, False, []

    def dollar(quoted):
        nonlocal i, unknown
        nxt = s[i + 1:i + 2]
        if nxt == "(" and s[i + 2:i + 3] != "(":
            toks, i = _lex(s, i + 2, True)
            subs.append(toks)
            add("$(...)", True)
        elif nxt in ("(", "{"):
            end = _closing(s, i + 1, nxt, ")" if nxt == "(" else "}")
            if end is None:
                raise Unparsed(f"an unclosed `${nxt}`")
            add(s[i:end], True)
            i = end
        elif nxt and (nxt.isalnum() or nxt in "_@*#?-$!"):
            end = i + 2
            while (nxt.isalpha() or nxt == "_") and end < n and (s[end].isalnum() or s[end] == "_"):
                end += 1
            add(s[i:end], True)
            i = end
        elif nxt in ("'", '"') and not quoted:  # `$'...'`
            i += 1
        else:
            add("$", True)
            i += 1
            return
        unknown = True

    def skip_heredocs():
        nonlocal i
        for delimiter, tabs in pending:
            while True:
                if i >= n:
                    break
                end = s.find("\n", i)
                line, i = s[i:n if end < 0 else end], n if end < 0 else end + 1
                if (line.lstrip("\t") if tabs else line) == delimiter or line.strip() == delimiter:
                    break
        pending.clear()

    while i < n:
        c = s[i]
        if c == "\\":
            if s[i + 1:i + 2] != "\n":  # a backslash-newline joins the lines
                add(s[i + 1:i + 2] or "\\", True)
            i += 2
        elif c == "'":
            end = s.find("'", i + 1)
            if end < 0:
                raise Unparsed("an unclosed single quote")
            add(s[i + 1:end], True)
            i = end + 1
        elif c == '"':
            add("", True)
            i += 1
            while s[i:i + 1] != '"':
                if i >= n:
                    raise Unparsed("an unclosed double quote")
                if s[i] == "\\" and s[i + 1:i + 2] in ("$", "`", '"', "\\", "\n"):
                    add(s[i + 1].replace("\n", ""), True)
                    i += 2
                elif s[i] == "$":
                    dollar(True)
                else:
                    if s[i] == "`":
                        unknown = True
                    add(s[i], True)
                    i += 1
            i += 1
        elif c == "$":
            dollar(False)
        elif c == "`":
            unknown = True
            add(c, True)
            i += 1
        elif c in " \t":
            end_word()
            i += 1
        elif c == "\n":
            end_word()
            i += 1
            skip_heredocs()
            out.append(";")
        elif c == "#" and chars is None:
            end = s.find("\n", i)
            i = n if end < 0 else end
        elif c in ";&|<>()":
            end_word()
            if c == ")" and closing and depth == 0:
                return out, i + 1
            if s.startswith("<<", i) and not s.startswith("<<<", i):
                heredoc = s.startswith("<<-", i)
                i += 3 if heredoc else 2
                continue
            op = next(sym for sym in SYMBOLS if s.startswith(sym, i))
            i += len(op)
            depth = depth + 1 if op == "(" else max(depth - 1, 0) if op == ")" else depth
            out.append(";" if op in CASE_ENDS else op)
        else:
            add(c, False)
            i += 1
    end_word()
    if closing:
        raise Unparsed("an unclosed `$(`")
    return out, i


class Command(list):
    """A simple command's words. `then`: the operator that follows it (`&&`, `||`, `;`, `|` ...), None at the end."""

    def __init__(self, words=(), then=None):
        super().__init__(words)
        self.then = then


def scoped_commands(toks):
    """[(words, scope)] — simple commands (Command) with redirections kept as words. `scope` names the shell a command runs in:
    () for the command line's own shell, a longer tuple inside ( ... ), on either side of a pipe and for a command
    sent to the background, and for the commands of a `$( ... )` (Word.subs), which stand before the command that holds
    them. A `cd` changes the directory of its own scope and of the scopes that begin after it."""
    cmds, cur, stack, count, piped = [], [], [()], 0, False

    def flush(own, then=None):
        nonlocal count, cur
        if cur:
            count += own
            scope = stack[-1] + ((count,) if own else ())
            for inner in (sub for w in cur if isinstance(w, Word) for sub in w.subs):  # each `$( ... )` runs first, in a shell of its own
                count += 1
                cmds.extend((words, scope + (count,) + within) for words, within in scoped_commands(inner))
            cmds.append((Command(cur, then), scope))
        cur = []

    for t in toks:
        if t in OPERATORS and not isinstance(t, Word):
            flush(piped or t in PIPES or t == "&", t)
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


def command_words(words, strict=False, files=None):
    """A simple command's own words, the program first: without the variable assignments, the compound command's
    opener (`{`, `then`, `do`, `!` ...) and the wrappers (`command`, `env`, `sudo`, `xargs`, `timeout` ...) before it.
    So `if true; then FOO=1 env touch x; fi` reads as `touch x`. [] when nothing is left (`do`, `fi`, `}` alone).
    `strict` (the grammar): no opener is skipped, and a wrapper's option outside its exact sets (WRAPPERS,
    WRAPPER_FLAGS) raises Unparsed, since it could take the next word and so hide the program. `files` (a list) is
    given the file a wrapper creates before it runs the command (WRAPPER_FILES: the lock file of `lockf`, `flock`)."""
    while words:
        name = Path(words[0]).name
        if ASSIGNMENT.fullmatch(words[0]) or (not strict and words[0] in OPENERS):
            words = words[1:]
        elif not strict and words[0] == "function":  # `function f { ...`
            words = words[2:]
        elif name in WRAPPERS:
            words = words[1:]
            while words and (words[0].startswith("-") or (name == "env" and ASSIGNMENT.fullmatch(words[0]))):
                takes_next = words[0] in WRAPPERS[name]
                known = takes_next and words[0] not in WRAPPER_MOVES.get(name, ()) or words[0] == "--" or ASSIGNMENT.fullmatch(words[0]) \
                    or re.fullmatch(WRAPPER_FLAGS.get(name, r"(?!)"), words[0])
                if strict and not known:
                    raise Unparsed(f"`{name} {words[0]}`: an option of `{name}` the guard does not know could take the next "
                                   f"word or change directory, so it cannot tell which program runs (write the command without it)")
                words = words[1 + takes_next:]
            if files is not None and name in WRAPPER_FILES:
                files += words[:1]
            words = words[WRAPPER_ARGUMENTS.get(name, 0):]
        else:
            break
    return words


# What the grammar refuses as a command's first word (after its wrappers too): the shell's own syntax ...
KEYWORDS = {"if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "select", "function",
            "{", "}", "!", "[[", "]]", "coproc", "repeat", "foreach", "end", "always"}
# ... and the builtins and programs whose effect a guard cannot read from their words
REFUSED_COMMANDS = {
    "runs text or a file as commands the guard does not read": ("eval", "source", ".", "trap", "emulate", "sched", "zpty", "script",
                                                                 "apply", "compgen", "complete", "compctl", "bind", "zle", "r"),
    "changes which program a name runs": ("alias", "unalias", "hash", "unhash", "rehash", "enable", "disable", "functions",
                                          "unfunction", "autoload", "zcompile", "zmodload"),
    "changes how the shell reads or runs a line": ("set", "setopt", "unsetopt", "shopt", "zstyle"),
    "changes directory in a way the guard does not follow (it follows only `cd <path>`)": ("pushd", "popd", "chdir", "chroot"),
    "sets a variable or writes a file in a way the guard does not read": ("print", "let", "vared", "zparseopts", "zformat",
                                                                           "zregexparse", "getopts", "history", "fc"),
}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish"}
# How a program's name begins. Anything else before a command is the shell's own (zsh's `-` modifier, a job `%1`), so
# the guard would take it for a program and not look at the one that runs. `[` and `:` are programs by name.
PROGRAM_NAME = re.compile(r"[A-Za-z0-9_/.]|~/|\[$|:$")
FUNCTION_DECLARERS = {"typeset", "declare", "local", "export", "readonly"}  # with an option that holds `f` they load or show functions
CALLBACK_READERS = {"mapfile", "readarray"}  # with an option that holds `C` they run a command for the lines they read
# How the path of a `cd` begins, like a program's name: zsh reads `cd +1` and `cd -1` as an entry of its directory stack
CD_PATH = re.compile(r"[A-Za-z0-9_/.]|~/")


def is_redirect(token):
    """True for an operator of `tokens` that takes the next word as its file (never a quoted `">"`: that is a Word)."""
    return token in (">", ">>", "<") and not isinstance(token, Word)


def simple_command(raw, piped=False):
    """(own words, the words before them, the files its redirects write) of one simple command of `tokens`, read by
    GRAMMAR; Unparsed when it is outside it. The own words begin with the program (`command_words`, strict); the words
    before them are its variable assignments and wrappers. Refused: a keyword of the shell or a REFUSED_COMMANDS name
    as the command; a program's name that does not begin as one (PROGRAM_NAME); a program's name, a wrapper's word or
    a word of a command the guard reads word by word (WRITTEN_OUT) that the shell expands or `xargs` supplies; a
    shell not run as `<shell> <script file>`; a `cd` that is not `cd <path written out>` as a command of its own
    (`piped`: it is a member of a pipe, where zsh runs the last one in the line's own shell); a declaring builtin
    given a function option, `mapfile -C`; `find -execdir`; a `sed` that may write a file or run a program
    (`sed_problem`). The files written include the one a wrapper creates (WRAPPER_FILES)."""
    words, written, redirected = [], [], False
    for before, w in zip([None, *raw], raw):
        if is_redirect(w):
            redirected = True
        elif is_redirect(before):
            written += [w] if before != "<" else []
        else:
            words.append(w)
    own = command_words(words, strict=True, files=written)
    before = words[:len(words) - len(own)]
    if own and "xargs" in {Path(w).name for w in before}:
        own = own + [Word("<what xargs reads>", unknown=True)]  # it appends or inserts words the guard cannot see
    leading = len(list(takewhile(ASSIGNMENT.fullmatch, words)))
    for w in dict.fromkeys(words[leading:leading + 1] + own[:1]):
        if w in KEYWORDS:
            raise Unparsed(f"`{w}` as a command: the shell's own syntax (loops, conditions, groups, functions) is not read. "
                           f"Write one plain command after the other")
        if why := next((why for why, names in REFUSED_COMMANDS.items() if Path(w).name in names or w == "."), None):
            raise Unparsed(f"`{w}` {why}")
    if expanded := next((w for w in before[leading:] + own[:1] if unresolved(w) or getattr(w, "glob", False)), None):
        raise Unparsed(f"the shell expands `{expanded}`, so the guard cannot tell which program runs (write it out)")
    if strange := next((w for w in words[leading:leading + 1] + own[:1] if not PROGRAM_NAME.match(w)), None):
        raise Unparsed(f"`{strange}` where a program's name stands: a name begins with a letter, a digit, `_`, `/`, `.` or "
                       f"`~/`. Anything else there is the shell's own (zsh reads `-` before a command as a modifier), so "
                       f"the guard cannot tell which program runs")
    if not own:
        return own, before, written
    name, expanded = Path(own[0]).name, next((w for w in own[1:] if unresolved(w) or getattr(w, "glob", False)), None)
    if name in SHELLS and (len(own) < 2 or own[1].startswith(("-", "+")) or own[1] == expanded
                           or os.path.realpath(os.path.expanduser(own[1])).startswith("/dev/")):
        raise Unparsed(f"`{name}` is read only as `{name} <script file> ...`: given `-c`, `-s`, an option or no script, "
                       f"it runs text the guard does not read (write the commands themselves)")
    if name == "cd" and (before or piped or redirected or len(own) != 2 or not CD_PATH.match(own[1]) or expanded
                         or getattr(own[1], "extended", False)):
        raise Unparsed("this `cd`: it is followed only as `cd <path written out>`, a command of its own (no option, no "
                       "`cd -`, no bare `cd`, not in a pipe, not behind `builtin` or `command`), the path beginning "
                       "with a letter, a digit, `_`, `/`, `.` or `~/`")
    if name in FUNCTION_DECLARERS and (option := next((w for w in own[1:] if re.fullmatch(r"[-+]\w*f\w*", w)), None)):
        raise Unparsed(f"`{name} {option}`: with a function option `{name}` loads or changes the shell's functions (zsh's "
                       f"`typeset -fu` is `autoload`), which changes which program a name runs")
    if name in CALLBACK_READERS and (option := next((w for w in own[1:] if re.fullmatch(r"-\w*C\w*", w)), None)):
        raise Unparsed(f"`{name} {option}`: with `-C` `{name}` runs a command for the lines it reads, which the guard "
                       f"does not read")
    if name == "find" and any(w.startswith("-files0-from") for w in own):
        raise Unparsed("`find -files0-from` takes its start paths from a file the guard does not read (name the paths)")
    if name == "find" and (primary := next((w for w in own if w in ("-execdir", "-okdir")), None)):
        raise Unparsed(f"`find {primary}` runs its command in the folder of each file it finds, which the guard does "
                       f"not follow (use `-exec` with the full path `{{}}`)")
    if name in WRITTEN_OUT and expanded:
        raise Unparsed(f"`{name}` is read word by word, so each must be written out: the shell expands `{expanded}` "
                       f"(a `$NAME`, a pattern). Quote a pattern meant for `{name}` itself")
    if name == "sed" and (problem := sed_problem(own)):
        raise Unparsed(f"this `sed`: {problem}")
    return own, before, written


def redirect_targets(words):
    """The files the redirections among `words` write: the word after `>`, `>>`, `>|`, `&>`, `&>>` or `>&`, unless
    that names a file descriptor (`>&2`, `2>&1`, `>&-`)."""
    return [t for w, t in zip(words, words[1:])
            if w in REDIRECTS and not isinstance(w, Word) and not (w == ">&" and re.fullmatch(r"\d+|-", t))]


def write_targets(words):
    """The paths the simple command `words` (its own words: `command_words`) writes by what it is, redirections
    aside: the arguments of a file-writing program (rm, cp, touch, chmod ... and sed/perl in place) and the values of
    its `--option=<value>` words, `dd of=`, and the start paths of a `find` that deletes or runs such a program. Of
    `cp <from> ... <to>` only `<to>` is written, unless it links (`-l`, `-s`); of `ln`, the link and what it points to
    (`ln_targets`)."""
    if not words:
        return []
    name = Path(words[0]).name
    args = [w for w in words[1:] if not w.startswith("-")]
    # a path may also be an option's value: `mktemp --tmpdir=<dir>`, GNU's `cp --target-directory=<dir>` and `cp -t<dir>`
    args += [w.split("=", 1)[1] for w in words[1:] if w.startswith("--") and "=" in w]
    args += [w[2:] for w in words[1:] if re.fullmatch(r"-[A-Za-z](.*/.*|\.\.)", w)]
    if name == "chmod":
        return list(dropwhile(lambda w: MODE.fullmatch(w) or w == ",", args))  # the tokenizer cuts a mode at its commas
    if name in ("chown", "chgrp", "chflags"):
        return args[1:]
    if name == "ln":
        return ln_targets(words)
    if name == "cp" and not any(re.match(r"-\w*[tls]", w) or abbreviates(w, ("--target-directory", "--link", "--symbolic-link"))
                                for w in words[1:]):
        # the files before it are read; GNU's `-t <dir>` names the folder first, and `-l` or `-s` makes links, through
        # which a later write lands in the source: then every word counts. GNU takes a long option by its beginning
        return args[-1:]
    if name in FILE_WRITERS or (name in ("sed", "perl") and any(re.fullmatch(r"-\w*i\w*", w) or w.startswith(("-i", "--in-place"))
                                                                 for w in words[1:])):
        return args
    if name == "dd":
        return [w[3:] for w in words[1:] if w.startswith("of=")]
    if name == "find":
        files = [t for w, t in zip(words, words[1:]) if w in FIND_FILES]
        if "-delete" in words or any(write_targets(command_words(run)) for _, run in find_runs(words)):
            return files + find_starts(words)
        return files
    return []


SED_FLAGS = re.compile(r"--(quiet|silent|regexp-extended|null-data|separate|unbuffered|posix|debug|sandbox|follow-symlinks|"
                       r"in-place(=.*)?)")
# The only sed scripts read in full: commands parted by `;` or a new line, each after up to two addresses (a line
# number, `$`, `/regex/`) and `!`: `s<d>a<d>b<d>` with the flags g, p, i, I and a number, or one of the commands
# that take nothing. No `[` in a regex: sed reads a delimiter inside brackets as text, and the guard would not.
_SED_ADDRESS = r"(?:\d+|\$|/(?:\\.|[^/\\\[\n])*/)"
_SED_PLAIN = re.compile(rf"(?:\s*(?:{_SED_ADDRESS}(?:\s*,\s*{_SED_ADDRESS})?)?\s*!?\s*"
                        r"(?:s([/|,#:@%_])(?:\\.|(?!\1)[^\\\[\n])*\1(?:\\.|(?!\1)[^\\\n])*\1[gpiI0-9]*|[pdDgGhHnNPx=l]|q\d*)"
                        r"\s*(?:;|\n|$))+")


def sed_problem(words):
    """Why the `sed ...` command `words` may write a file or run a program by its script, or None. sed writes with
    `w file`, `W file` and the `w` flag of `s`, and GNU sed runs a program with `e`. The guard does not read sed's
    language: a script passes when it is one of the plain forms (_SED_PLAIN), where a `w` or an `e` can only be text
    of `s///` or of an address, or when it holds neither letter at all. Without `-e` the script is the first word
    that is no option; after a bare `-i` BSD sed takes that word as a suffix and the next as the script, so both are
    held to the rule."""
    scripts, rest, bare_in_place, i = [], [], False, 1
    while i < len(words):
        w = words[i]
        i += 1
        if w == "--":
            rest += words[i:]
            break
        if w == "--expression" or w.startswith("--expression="):
            scripts.append(w.split("=", 1)[1] if "=" in w else "".join(words[i:i + 1]))
            i += "=" not in w
        elif w.startswith("--"):
            if not SED_FLAGS.fullmatch(w):
                return f"`{w}` is not an option of sed the guard knows (a script from a file, `-f`, is not read)"
        elif w.startswith("-") and len(w) > 1:
            for k, c in enumerate(w[1:], 2):  # a group of short options; k: where the rest of the word begins
                if c == "e":
                    scripts.append(w[k:] or "".join(words[i:i + 1]))
                    i += not w[k:]
                elif c in "iI":
                    bare_in_place = bare_in_place or not w[k:]
                elif c not in "EnrsuzaH":  # not `l`: GNU's `-l <N>` takes the next word, BSD's takes none
                    return f"`{w}` is not an option of sed the guard knows (a script from a file, `-f`, is not read)"
                if c in "eiI":
                    break  # the rest of the word was its value
        else:
            rest.append(w)
    for script in scripts or rest[:2 if bare_in_place else 1]:
        if re.search("[wWe]", script) and not _SED_PLAIN.fullmatch(script):
            return (f"the script `{script}` holds a `w` or an `e`, which may be sed's command to write a file or run a "
                    f"program, and is not one of the plain forms the guard reads in full (`s/a/b/g`, `/regex/p`, "
                    f"`2,5d`, parted by `;`, no `[` in a regex). Write it in such a form, use `grep`, or send sed's "
                    f"output to a file with `>`")
    return None


def ln_targets(words):
    """The paths `ln ...` writes: the link, which is its last argument (a folder there holds the link instead, as
    does the folder after `-t`), and what the link points to, since a later write through the link lands there. A
    symbolic link resolves a relative target from the folder that holds it, not from where the shell stands: the
    target is given joined to the last argument and to the folder that holds it, whichever the link turns out to be."""
    options = [w for w in words[1:] if w.startswith("-")]
    names = [w for w in words[1:] if not w.startswith("-")]
    symbolic = "--symbolic" in options or any(re.fullmatch(r"-[A-Za-z]*s[A-Za-z]*", o) for o in options)
    folder = next((t for w, t in zip(words, words[1:]) if w == "-t"), None)
    folder = folder or next((m.group(1) for o in options if (m := re.fullmatch(r"(?:--target-directory=|-t)(.+)", o))), None)
    if folder:
        folders, targets = [folder], [n for n in names if n is not folder]
    elif len(names) > 1:
        folders, targets = [names[-1], os.path.dirname(names[-1]) or "."], names[:-1]
    else:
        folders, targets = ["."], names  # `ln -s <target>`: the link is made where the shell stands
    out = folders[:1]
    for target in targets:
        relative = not os.path.isabs(os.path.expanduser(target))
        out += [os.path.join(f, target) for f in folders] if symbolic and relative else [target]
    return out


def find_runs(words):
    """[(primary, words)] of the commands a `find` runs: after `-exec` or `-ok` (the grammar refuses `-execdir` and
    `-okdir`), up to `;` or `+`."""
    return [(w, list(takewhile(lambda x: x not in (";", "+"), words[i + 1:]))) for i, w in enumerate(words) if w in FIND_RUNS]


def find_starts(words):
    """The paths a `find` command starts from: after its options, before its first primary; "." when it names none."""
    rest, named = list(words[1:]), []
    while rest and (m := FIND_OPTIONS.fullmatch(rest[0])):  # its options stand before the paths
        rest = rest[1:]
        if m.group(0) == "--":
            break
        if m.group("takes"):  # -D <debug> (GNU), -f <path> (BSD): the rest of the word, or the next word
            word = m.group("attached") or (rest.pop(0) if rest else "")
            if m.group("takes") == "f":
                # `-follow` is a path to BSD and a primary over "." to GNU: both are kept
                named += [word, "."] if m.group("attached") else [word]
    return named + list(takewhile(lambda w: not w.startswith(("-", "(", "!")), rest)) or ["."]


def scope_dir(dirs, scope):
    """The directory the shell `scope` stands in (None: unknown), from `dirs` ({scope: directory}, filled as scopes
    are met): a new scope begins where its parent stands at that moment."""
    if scope not in dirs:
        dirs[scope] = scope_dir(dirs, scope[:-1])
    return dirs[scope]


UNRESOLVED = re.compile(r"[$`]|^~[^/]")
CD_COMMANDS = ("cd", "pushd", "popd")  # what `cd_target` reads; the grammar takes only `cd <path>` (simple_command, Standing)


def unresolved(path):
    """True when the shell would expand `path` (variable, command substitution, `~user`), which a hook cannot follow.
    A word from the lexer knows it (a quoted `'$x'` is plain text); any other string is judged by its characters."""
    return path.unknown if isinstance(path, Word) else bool(UNRESOLVED.search(str(path)))


# Variables no command line of the coordinator may set, in any form: they move git to another repo or make it run a
# program (every GIT_* name, the pagers and editors, less's filters, the askpass programs), choose which program or
# library a name stands for (PATH, the loader's variables, a shell's startup file), move a path the guard resolves
# itself (HOME for `~`, CDPATH, TMPDIR), or hold text the shell runs or reads a line by (PS4, PROMPT_COMMAND, IFS,
# zsh's NULLCMD). git and every program started from the line would follow them.
GUARDED_VARIABLES = (r"GIT_\w*|PAGER|MANPAGER|EDITOR|VISUAL|LESSOPEN|LESSCLOSE|SSH_ASKPASS|SUDO_ASKPASS|PATH|HOME|CDPATH|"
                     r"XDG_CONFIG_HOME|LD_PRELOAD|LD_LIBRARY_PATH|DYLD_\w*|BASH_ENV|ENV|ZDOTDIR|PS4|PROMPT_COMMAND|"
                     r"TMPDIR|TMPPREFIX|IFS|NULLCMD|READNULLCMD|FPATH|MANPATH")
# the name as a word of its own or as part of one (`GIT_DIR=x`), but not where it is only read (`$PATH`); and
# attached to an option's letters (`printf -vGIT_DIR`)
def _names(variables):
    return re.compile(rf"(?<![A-Za-z0-9_$])(?<!\$\{{)({variables})(?![A-Za-z0-9_])|^-[A-Za-z]*?({variables})(?![A-Za-z0-9_])")


NAMES_GUARDED = _names(GUARDED_VARIABLES)
NAMES_GIT = _names(r"GIT_\w*")  # what a hat that does not edit may not set on a line that runs git (hwdeny)
NAMES_HOME = _names("HOME")  # after it a hat that does not edit names no path by `~` (hwdeny)
TIED_ARRAYS = re.compile(r"(path|cdpath|fpath|manpath|module_path|mailpath)\+?=.*", re.S)  # zsh ties them to the upper-case names
# builtins that set the variables their arguments name: they take only words written out (WRITTEN_OUT)
DECLARERS = {"export", "declare", "typeset", "readonly", "local", "integer", "float", "read", "mapfile", "readarray", "unset"}
# Commands a guard reads word by word, so the shell may expand none of their words (GRAMMAR: a `$NAME` or a pattern
# never reaches a path that is written): file writers, the editors in place, `find`, and the builtins above. git has
# its own rule (the guards' git checks).
WRITTEN_OUT = FILE_WRITERS | DECLARERS | {"sed", "perl", "dd", "find", "cd"}
# Programs that cannot set a variable of the shell, whatever their words: a guarded name among them is only text
# (`grep -rn GIT_DIR bin`, `git log --grep=PATH`). So is any program named by a path (`bin/hw`).
SETS_NOTHING = FILE_WRITERS | {"echo", "cat", "grep", "egrep", "fgrep", "rg", "ls", "head", "tail", "wc", "sort", "uniq", "cut", "tr",
                               "awk", "sed", "jq", "diff", "find", "git", "gh", "herdr", "python3", "printenv", "test", "["}


def guarded_variable(words, named=NAMES_GUARDED):
    """The guarded variable the simple command `words` (as written, redirections aside) names where the shell could
    set, export, declare or unset it: anywhere before the program (`X=... cmd`, `env X=...`, `env -u X`), and anywhere
    after a program that is not known to set nothing (`export X`, `declare -x X=...`, `read X`, `printf -v X`, zsh's
    `typeset -T`, any builtin nobody listed). Reading it (`$PATH`) is not setting it. None when it names none.
    `named`: the names that count (NAMES_GUARDED, or NAMES_GIT or NAMES_HOME for those alone)."""
    own = command_words(words)
    before = words[:len(words) - len(own)]
    text_only = bool(own) and ("/" in own[0] or own[0] in SETS_NOTHING)
    for w in before if text_only else words:
        if m := named.search(w) or (named is NAMES_GUARDED and TIED_ARRAYS.fullmatch(w)):
            return next(filter(None, m.groups()))
    return None


def cd_target(words, cwd):
    """Where the `cd`, `pushd` or `popd` command `words` leaves a shell that stood in `cwd`, if it succeeds; None when
    that cannot be known (a target the shell expands, `cd -`, `popd`, a bare `pushd`, which swaps the two directories
    on top of the stack, an unknown `cwd` with a relative target, zsh's `cd <old> <new>`). A bare `cd` leads home.
    It says where a `cd` aims, and is what the loose reading follows (a hat that edits); whether a guard that reads by
    the grammar follows the `cd` is `cd_followed`."""
    args = [w for w in words[1:] if w != "--" and not re.fullmatch(r"-[LPe@]+", w)]
    if len(args) == 1 and not unresolved(args[0]) and args[0] != "-":
        d = Path(os.path.expanduser(args[0]))
        if d.is_absolute() or cwd is not None:
            return os.path.realpath(d if d.is_absolute() else Path(cwd) / d)
        return None
    return os.path.realpath(os.path.expanduser("~")) if not args and Path(words[0]).name == "cd" else None


def can_enter(path):
    """Whether `path` is a directory the caller can enter now."""
    return os.path.isdir(path) and os.access(path, os.X_OK)


def cd_followed(words, cwd, then):
    """Where the grammar's `cd <path>` leaves a shell that stood in `cwd`, when the guard can be sure that what runs
    after it runs there; None (unknown) otherwise. Sure means: `&&` follows (`then`), so nothing after the `cd` runs
    unless it succeeded; the path holds no `..`, which the shell and the kernel read differently after a link; and
    the path as written, walked from `cwd`, is a directory the caller can enter when the line is checked (asked
    before anything is resolved: `missing/..` is no directory)."""
    path = os.path.expanduser(words[1])
    if then != "&&" or ".." in Path(path).parts or (cwd is None and not os.path.isabs(path)):
        return None
    path = os.path.join(cwd or "/", path)
    return os.path.realpath(path) if can_enter(path) else None


class Standing:
    """Where the shells of one command line stand, as far as a guard can be sure (None: unknown). A guard that reads
    by the grammar (`strict`) does not model what a line does to the folders it walks. It follows a `cd` only at the
    head of the line, where nothing ran that could have changed the path or set `CDPATH`, and only as `cd <path> &&`
    (`cd_followed`);
    and it follows it only up to the first `;`, `||` or new line, since what stands after one runs whether or not the
    `cd` succeeded. After any other `cd` the directory is unknown to the end of the line, in every shell of it: a
    guard then refuses a relative write and every git command. A hat that edits is read loosely (`cd_target`)."""

    def __init__(self, cwd, strict=True):
        self.dirs, self.strict = {(): cwd}, strict  # dirs: {scope: directory}, see scope_dir
        self.head, self.met_cd, self.lost = True, False, False  # only followed `cd`s so far; a `cd` was met; the directory is unknown from here on

    def at(self, scope):
        return None if self.lost else scope_dir(self.dirs, scope)

    def after(self, words, scope, then):
        """Take in the command `words` (its own words: `command_words`; none for a bare `X=1`) of the shell `scope`,
        which the operator `then` follows."""
        is_cd = bool(words) and Path(words[0]).name in CD_COMMANDS
        if is_cd and not self.strict:
            self.dirs[scope] = cd_target(words, self.at(scope))
        elif is_cd:
            self.dirs[scope] = cd_followed(words, self.at(scope), then) if self.head else None
            self.met_cd, self.lost = True, self.lost or self.dirs[scope] is None
        else:
            self.head = False
        if self.strict and self.met_cd and then not in ("&&", "|"):
            self.lost = True


# File writers that make a plain file or folder where there was none, change a file's content, or only take a path
# away or close it: a path leads after them where it led before, or nowhere. Any other writer (`ln`, `mv`, `cp`,
# `rsync` ...) may leave a link or another folder where the guard saw one.
PLAIN_MAKERS = {"mkdir", "touch", "tee", "truncate", "mktemp", "mkfifo", "mknod"}
REMOVERS = {"rm", "rmdir", "unlink", "shred", "chmod", "chown", "chgrp", "chflags"}


def _absolute(path, cwd):
    path = os.path.expanduser(str(path))
    return path if os.path.isabs(path) else None if cwd is None else os.path.join(cwd, path)


def _spellings(path):
    """`path` as written, without its `.` and `..`, and as it resolves now."""
    return {os.path.normpath(path), os.path.realpath(path)}


def changed_paths(words, cwd):
    """The paths that may lead elsewhere once the simple command `words` (its own words) has run in `cwd`: what a
    file writer other than PLAIN_MAKERS and REMOVERS names (a link made or replaced, a folder moved or copied). The
    guards resolve every path before the line runs, so what a later command of the line does through such a path
    cannot be checked (`walks_through`)."""
    if not words or Path(words[0]).name in PLAIN_MAKERS | REMOVERS:
        return set()
    return {s for w in write_targets(words) if not unresolved(w) and (p := _absolute(w, cwd)) for s in _spellings(p)}


def walks_through(path, cwd, changed, at=True):
    """Whether `path`, walked from `cwd` as the shell will walk it (a `..` after a link leads out of the link's
    target, not out of its name), passes through one of the paths `changed` (`changed_paths`). `at`: the path itself
    counts, not only the folders it passes through."""
    path = _absolute(path, cwd)
    if path is None or not changed:
        return False
    parts = Path(path).parts
    return any(s in changed for k in range(1, len(parts) + at) for s in _spellings(os.path.join(*parts[:k])))


def git_command(words, cwd):
    """(subcommand, its arguments, repo dir) of a `git ...` command, skipping global options. The repo dir follows
    `-C`, `--git-dir` and `--work-tree`; None when it cannot be known (a relative path from an unknown directory)."""
    i, target = 1, cwd

    def moved(path):
        if os.path.isabs(os.path.expanduser(path)):
            return path
        return None if target is None else str(Path(target) / path)
    while i < len(words):
        w = words[i]
        if w in ("-C", "--git-dir", "--work-tree") and i + 1 < len(words):
            target = moved(words[i + 1])
            i += 2
        elif w.startswith(("--git-dir=", "--work-tree=")):
            target = moved(w.split("=", 1)[1])
            i += 1
        elif w == "-c" and i + 1 < len(words):
            i += 2
        elif w.startswith("-"):
            i += 1
        else:
            return w, words[i + 1:], target
    return None, [], target


# git commands that only read. Anything else run in a repo the coordinator may not change is refused (hw-pretool).
# It is an allow list strict by construction, so a writing form nobody thought of is refused too:
#   * global options: only GIT_GLOBAL_FLAGS (none takes a value) and `-C <dir>` (the dir is where the guard checks);
#     `-c`, `--config-env`, `--exec-path`, `--git-dir` ... are refused (a config value can name a program to run);
#   * the subcommand is the first word after them, and a listing word (`list`, `show`, `get`, `--list` ...) comes right
#     after the subcommand;
#   * every option after that is in the form's own set: an exact spelling that takes no value, `--opt=<value>` only for
#     the names listed, or an attached short form (`-5`, `-n5`). An option that takes the next word as its value is
#     never listed, so no word can be swallowed: a write can never pass as a read's option value;
#   * positional words only where the read takes them, and only as many.
# No listed option names a program to run (`--upload-pack`, `--exec`, `--ext-diff`, `--textconv`, `--output`, `-O` ...).
# Some harmless reads are refused (`git log -n 5`: write `-n5`; `git grep -e x`: write `git grep x`).
GIT_GLOBAL_FLAGS = {"--no-pager", "-P", "--no-optional-locks", "--literal-pathspecs", "--no-replace-objects", "--version",
                    "--help"}
ANY = None  # any number of positional words


class ReadForm:
    """One read form of a subcommand: its no-value options, the `--name=` options, attached short forms (regex), and
    how many positional words it takes (ANY, or a count) and what they look like (regex)."""

    def __init__(self, flags=(), values=(), attached=(), positionals=0, names=r".+"):
        self.flags, self.values = frozenset(flags), frozenset(values)
        self.attached = re.compile("|".join(attached)) if attached else None
        self.positionals, self.names = positionals, re.compile(names)

    def problem(self, args):
        """Why `args` are not this read, in words the caller can act on; None when they are."""
        positional, ended = [], False
        for a in args:
            if ended or not a.startswith("-") or a == "-":
                positional.append(a)
            elif a == "--":
                ended = True
            elif not (a in self.flags or a.split("=", 1)[0] in self.values and "=" in a
                      or self.attached and self.attached.fullmatch(a)):
                return (f"`{a}` is not an option the guard takes here. It takes an option only in a form that cannot "
                        f"swallow the next word: its value attached (`-n5`, `-U5`, `--grep=x`, `--format=%h`), short "
                        f"options one by one (`-s -n`), the listing word right after the subcommand (`branch --list -v x`)")
        if self.positionals is not ANY and len(positional) > self.positionals:
            return (f"`{positional[self.positionals]}` is one name too many for the reading form" if self.positionals else
                    f"the reading form takes no name, so `{positional[0]}` makes it a write")
        if bad := next((p for p in positional if not self.names.fullmatch(p)), None):
            return f"`{bad}` is not a name the reading form takes (a ref's reflog: `reflog show <ref>`; a config key: `section.name`)"
        return None

    def reads(self, args):
        return self.problem(args) is None

    def __or__(self, other):
        return ReadForm(self.flags | other.flags, self.values | other.values,
                        [p.pattern for p in (self.attached, other.attached) if p], ANY)


def _both(*names):
    """`--x` and `--no-x` for each name."""
    return {f"--{p}{n}" for n in names for p in ("", "no-")}


DIFF = ReadForm(
    {"-p", "-u", "--patch", "-s", "--no-patch", "--raw", "--patch-with-raw", "--patch-with-stat", "--stat", "--shortstat",
     "--numstat", "--compact-summary", "--summary", "--name-only", "--name-status", "--dirstat", "--cumulative", "-z",
     "--full-index", "--binary", "--abbrev", "--no-abbrev", "-B", "-M", "-C", "-D", "--irreversible-delete", "--find-copies-harder",
     "--find-renames", "--find-copies", "--no-renames", "--check", "-R", "--text", "-a", "--minimal", "--patience",
     "--histogram", "-w", "--ignore-all-space", "-b", "--ignore-space-change", "--ignore-space-at-eol",
     "--ignore-cr-at-eol", "--ignore-blank-lines", "--function-context", "-W", "--exit-code", "--quiet", "--no-prefix",
     "--default-prefix", "--no-ext-diff", "--no-textconv", "--ita-invisible-in-index", "--ita-visible-in-index",
     "--pickaxe-all", "--pickaxe-regex", "--word-diff", "--color-words", "--color-moved", "--no-color-moved",
     "--color-moved-ws", "--no-color-moved-ws", "--indent-heuristic", "--no-indent-heuristic", "--relative",
     "--no-relative", "--cached", "--staged", "--merge-base", "--no-index", "--submodule", "--ignore-submodules",
     "--cc", "-m", "-c", "--combined-all-paths", "--remerge-diff", "--diff-merges", "--no-diff-merges", "-t", "-r",
     "--root", "--stdin"} | _both("color", "rename-empty"),
    {"--stat", "--stat-width", "--stat-name-width", "--stat-graph-width", "--stat-count", "--dirstat", "--abbrev",
     "--unified", "--inter-hunk-context", "--diff-algorithm", "--diff-filter", "--color", "--word-diff",
     "--word-diff-regex", "--color-words", "--color-moved", "--color-moved-ws", "--relative", "--src-prefix",
     "--dst-prefix", "--line-prefix", "--find-renames", "--find-copies", "--break-rewrites", "--submodule",
     "--ignore-submodules", "--diff-merges", "--anchored", "--ws-error-highlight", "--output-indicator-new",
     "--output-indicator-old", "--output-indicator-context", "--max-depth"},
    [r"-U\d+", r"-[MCB]\d+%?", r"-l\d+", r"-S.+", r"-G.+"], ANY)
LOG = DIFF | ReadForm(
    {"--oneline", "--graph", "--all", "--branches", "--tags", "--remotes", "--decorate", "--no-decorate",
     "--clear-decorations", "--source", "--no-source", "--first-parent", "--merges", "--no-merges", "--reverse",
     "--topo-order", "--date-order", "--author-date-order", "--abbrev-commit", "--no-abbrev-commit", "--full-history",
     "--simplify-merges", "--simplify-by-decoration", "--dense", "--sparse", "--ancestry-path", "--left-right",
     "--left-only", "--right-only", "--cherry-pick", "--cherry-mark", "--cherry", "--boundary", "--follow",
     "--no-walk", "--do-walk", "--relative-date", "--parents", "--children", "--full-diff", "--log-size", "-q",
     "--use-mailmap", "--no-use-mailmap", "--mailmap", "--no-mailmap", "--not", "--all-match", "--invert-grep",
     "-i", "--regexp-ignore-case", "-E", "--extended-regexp", "-F", "--fixed-strings", "-P", "--perl-regexp",
     "--basic-regexp", "--count", "--objects", "--objects-edge", "--no-object-names", "--reflog", "--walk-reflogs",
     "-g", "--single-worktree", "--show-pulls", "--remove-empty", "--no-min-parents", "--no-max-parents",
     "--show-linear-break", "--notes", "--no-notes", "--standard-notes", "--no-standard-notes", "--expand-tabs",
     "--no-expand-tabs", "--bisect", "--in-commit-order", "--timestamp", "--header"},
    {"--max-count", "--skip", "--since", "--after", "--until", "--before", "--since-as-filter", "--author",
     "--committer", "--grep", "--grep-reflog", "--format", "--pretty", "--date", "--decorate", "--decorate-refs",
     "--decorate-refs-exclude", "--branches", "--tags", "--remotes", "--glob", "--exclude", "--min-parents",
     "--max-parents", "--encoding", "--notes", "--expand-tabs", "--show-linear-break", "--abbrev", "--ancestry-path"},
    [r"-\d+", r"-n\d+"], ANY)


def _read(flags=(), values=(), attached=(), positionals=ANY, names=r".+"):
    return ReadForm(flags, values, attached, positionals, names)


STATUS = _read({"-s", "--short", "-b", "--branch", "--show-stash", "--porcelain", "--long", "-z", "--null", "-v",
                "--verbose", "-u", "--untracked-files", "--ignored", "--no-renames", "--renames", "-M",
                "--find-renames"} | _both("ahead-behind", "column"),
               {"--porcelain", "--untracked-files", "--ignored", "--ignore-submodules", "--column", "--find-renames"},
               [r"-u(all|normal|no)", r"-M\d+%?"])
BRANCH_LIST = {"-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "-i", "--ignore-case", "--show-current",
               "--merged", "--no-merged", "--contains", "--no-contains", "--omit-empty", "--no-omit-empty"} | \
              _both("color", "column", "abbrev")
TAG_LIST = {"-i", "--ignore-case", "--merged", "--no-merged", "--contains", "--no-contains", "--omit-empty",
            "--no-omit-empty", "-n"} | _both("color", "column")
LIST_VALUES = {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--format", "--color",
               "--column", "--abbrev"}
CONFIG_SCOPE = {"--global", "--system", "--local", "--worktree", "--includes", "--no-includes", "--show-origin",
                "--show-scope", "-z", "--null", "--name-only", "--bool", "--int", "--bool-or-int", "--path",
                "--expiry-date"}
CONFIG_VALUES = {"--file", "--blob", "--type", "--default", "--value", "--url"}
CONFIG_KEY = r"[^.\s=]+\..+"  # a config key always has a section: `user.name`; `edit` or `set` never matches

# subcommand -> {listing word, or None for the bare form: the read form of the words after it}
GIT_READS = {
    "status": {None: STATUS},
    "log": {None: LOG}, "show": {None: LOG}, "whatchanged": {None: LOG}, "rev-list": {None: LOG},
    "shortlog": {None: LOG | _read({"-n", "--numbered", "-s", "--summary", "-e", "--email", "-c", "--committer"},
                                   {"--group", "--format"})},
    "diff": {None: DIFF}, "diff-tree": {None: DIFF}, "diff-index": {None: DIFF}, "diff-files": {None: DIFF},
    "rev-parse": {None: _read({"--abbrev-ref", "--short", "--verify", "--quiet", "-q", "--show-toplevel", "--git-dir",
                               "--absolute-git-dir", "--git-common-dir", "--is-inside-work-tree", "--is-inside-git-dir",
                               "--is-bare-repository", "--is-shallow-repository", "--show-prefix", "--show-cdup",
                               "--show-superproject-working-tree", "--symbolic", "--symbolic-full-name", "--all",
                               "--branches", "--tags", "--remotes", "--sq", "--not", "--revs-only", "--no-revs",
                               "--flags", "--no-flags", "--default", "--show-object-format", "--show-ref-format"},
                              {"--short", "--abbrev-ref", "--branches", "--tags", "--remotes", "--glob", "--exclude",
                               "--since", "--after", "--until", "--before", "--show-object-format", "--disambiguate"})},
    "ls-files": {None: _read({"-c", "--cached", "-d", "--deleted", "-m", "--modified", "-o", "--others", "-i",
                              "--ignored", "-s", "--stage", "-u", "--unmerged", "-k", "--killed", "-z", "-t", "-v", "-f",
                              "--directory", "--no-empty-directory", "--eol", "--full-name", "--exclude-standard",
                              "--error-unmatch", "--debug", "--recurse-submodules", "--deduplicate", "--sparse",
                              "--resolve-undo", "--abbrev"},
                             {"--exclude", "--exclude-per-directory", "--abbrev", "--format"})},
    "ls-tree": {None: _read({"-d", "-r", "-t", "-l", "--long", "-z", "--name-only", "--name-status", "--object-only",
                             "--full-name", "--full-tree", "--abbrev"}, {"--abbrev", "--format"})},
    "cat-file": {None: _read({"-t", "-s", "-e", "-p", "--batch-all-objects", "--unordered", "--follow-symlinks", "-Z",
                              "--allow-unknown-type", "--use-mailmap", "--no-use-mailmap", "--mailmap", "--no-mailmap"})},
    "blame": {None: _read({"-b", "--root", "--show-stats", "-l", "-t", "-s", "-e", "--show-email", "-n", "--show-name",
                           "--show-number", "-f", "-p", "--porcelain", "--line-porcelain", "-c", "--incremental",
                           "--progress", "--no-progress", "-w", "--minimal", "--abbrev", "-M", "-C", "--color-lines",
                           "--color-by-age"}, {"--abbrev", "--date"}, [r"-L.+", r"-M\d+", r"-C\d+"])},
    "grep": {None: _read({"-i", "--ignore-case", "-w", "--word-regexp", "-v", "--invert-match", "-n", "--line-number",
                          "--column", "-l", "--files-with-matches", "--name-only", "-L", "--files-without-match", "-c",
                          "--count", "-h", "-H", "--full-name", "-E", "--extended-regexp", "-F", "--fixed-strings",
                          "-G", "--basic-regexp", "-P", "--perl-regexp", "-z", "--null", "-o", "--only-matching", "-q",
                          "--quiet", "--cached", "--untracked", "--no-index", "--exclude-standard",
                          "--no-exclude-standard", "--recurse-submodules", "-a", "--text", "-I", "--break", "--heading",
                          "-p", "--show-function", "-W", "--function-context", "--all-match", "--and", "--or", "--not",
                          "(", ")", "-r", "--recursive", "--no-recursive", "--color", "--no-color"},
                         {"--max-depth", "--color", "--threads", "--max-count"}, [r"-\d+", r"-[ABCm]\d+"])},
    "for-each-ref": {None: _read({"--shell", "--perl", "--python", "--tcl", "--omit-empty", "--ignore-case",
                                  "--include-root-refs"},
                                 {"--count", "--sort", "--format", "--points-at", "--merged", "--no-merged",
                                  "--contains", "--no-contains", "--exclude", "--start-after"})},
    "merge-base": {None: _read({"-a", "--all", "--octopus", "--independent", "--is-ancestor", "--fork-point"})},
    "ls-remote": {None: _read({"-q", "--quiet", "-t", "--tags", "-b", "--branches", "-h", "--heads", "--refs",
                               "--get-url", "--exit-code", "--symref"}, {"--sort"})},
    "describe": {None: _read({"--all", "--tags", "--contains", "--long", "--always", "--first-parent", "--exact-match",
                              "--debug"},
                             {"--abbrev", "--candidates", "--match", "--exclude"})},
    "show-ref": {None: _read({"--head", "--verify", "--exists", "--branches", "--heads", "--tags", "-d", "--dereference",
                              "-s", "--hash", "-q", "--quiet", "--abbrev"}, {"--hash", "--abbrev"})},
    "name-rev": {None: _read({"--tags", "--all", "--name-only", "--undefined", "--no-undefined", "--always"},
                             {"--refs", "--exclude"})},
    "check-ignore": {None: _read({"-q", "--quiet", "-v", "--verbose", "-n", "--non-matching", "--no-index", "-z"})},
    "count-objects": {None: _read({"-v", "--verbose", "-H", "--human-readable"}, positionals=0)},
    "version": {None: _read({"--build-options"}, positionals=0)},
    "help": {None: _read({"-a", "--all", "-g", "--guides", "--config", "--user-interfaces", "--developer-interfaces",
                          "--no-external-commands", "--no-aliases", "--verbose"})},
    "branch": {None: _read(BRANCH_LIST, LIST_VALUES, positionals=0),
               "--list": _read(BRANCH_LIST, LIST_VALUES), "-l": _read(BRANCH_LIST, LIST_VALUES)},
    "tag": {None: _read(TAG_LIST, LIST_VALUES, [r"-n\d+"], positionals=0),
            "--list": _read(TAG_LIST, LIST_VALUES, [r"-n\d+"]), "-l": _read(TAG_LIST, LIST_VALUES, [r"-n\d+"])},
    "worktree": {"list": _read({"-v", "--verbose", "--porcelain", "-z"}, {"--expire"}, positionals=0)},
    "stash": {"list": ReadForm(LOG.flags, LOG.values, [LOG.attached.pattern], 0),
              "show": ReadForm(DIFF.flags | {"--include-untracked", "--only-untracked", "--no-include-untracked"},
                               DIFF.values, [DIFF.attached.pattern], 1)},
    "remote": {None: _read({"-v", "--verbose"}, positionals=0), "show": _read({"-n"}),
               "get-url": _read({"--push", "--all"}, positionals=1)},
    "config": {"get": _read(CONFIG_SCOPE | {"--all", "--regexp", "--fixed-value"}, CONFIG_VALUES, positionals=1),
               "list": _read(CONFIG_SCOPE, CONFIG_VALUES, positionals=0),
               "--get": _read(CONFIG_SCOPE | {"--fixed-value"}, CONFIG_VALUES, positionals=2),
               "--get-all": _read(CONFIG_SCOPE | {"--fixed-value"}, CONFIG_VALUES, positionals=2),
               "--get-regexp": _read(CONFIG_SCOPE | {"--fixed-value"}, CONFIG_VALUES, positionals=2),
               "--get-urlmatch": _read(CONFIG_SCOPE, CONFIG_VALUES, positionals=2),
               "-l": _read(CONFIG_SCOPE, CONFIG_VALUES, positionals=0),
               "--list": _read(CONFIG_SCOPE, CONFIG_VALUES, positionals=0),
               None: _read(positionals=1, names=CONFIG_KEY)},  # `git config user.name`
    # a bare `git reflog <word>` reads <word> as a ref unless git knows it as a subcommand, which a later git may add:
    # so only a word that cannot be a subcommand (`HEAD`, `main@{1}`, `refs/heads/x`, `origin/main`); `git reflog show
    # <ref>` reads any ref
    "reflog": {None: _read(positionals=1, names=r"[A-Z_]*HEAD|.*[/@].*"),
               "show": LOG, "list": _read(positionals=0), "exists": _read(positionals=1)},
    "notes": {None: _read(positionals=0), "list": _read(positionals=1), "show": _read(positionals=1)},
}


# In a repo the coordinator may change, what still makes git run a program named on the command line: a subcommand
# that takes one (GIT_RUNS: the word after it that makes it so, or None for any use), an option that names one, and
# a config key other than GIT_CONFIG_SET (an alias, an editor, a pager, a filter, a hook path ... is a program).
GIT_RUNS = {"filter-branch": None, "difftool": None, "mergetool": None, "send-email": None, "instaweb": None, "daemon": None,
            "web--browse": None, "bisect": "run", "submodule": "foreach", "rebase": "-x", "clone": "-u"}
GIT_RUN_OPTIONS = ("--exec", "--upload-pack", "--receive-pack", "--extcmd", "--open-files-in-pager")  # and `-O<pager>`
GIT_CONFIG_SET = {"user.name", "user.email"}


def abbreviates(word, names):
    """Whether the option `word` (`--name`, `--name=<value>`) is one of the long options `names` or a beginning of
    one: git takes a long option by any beginning that is unambiguous (`--exe` for `--exec`). The guard does not know
    which beginnings a subcommand takes, so every one of two letters or more counts."""
    name = word.split("=", 1)[0]
    return len(name) >= len("--") + 2 and any(n.startswith(name) for n in names)


def git_runs_a_program(sub, args):
    """Why `git <sub> <args>` runs a program named on the command line or sets configuration that names one, or
    None. Configuration already in the repo or the user's files is not seen here."""
    if sub in GIT_RUNS:
        word = GIT_RUNS[sub]  # a short option may hold its value and stand in a group (`-x<cmd>`, `-qx<cmd>`); `run` and `foreach` are words of their own
        if word is None or word in args or (word.startswith("-") and any(re.fullmatch(rf"-[A-Za-z]*{word[1]}.*", a, re.S) for a in args)):
            return f"`git {sub}{' ' + word if word else ''}` runs a program for each commit or file"
    if option := next((a for a in args if a.startswith("-O") or abbreviates(a, GIT_RUN_OPTIONS)), None):
        return f"`{option}` names a program for git to run"
    if sub == "config" and git_read_problem(["git", sub, *args]):
        keys = [a for a in args if not a.startswith("-") and a not in ("set", "unset", "edit", "rename-section", "remove-section")]
        if "edit" in args or "--edit" in args or "-e" in args or not keys or keys[0] not in GIT_CONFIG_SET:
            return ("`git config` here would set a key that can name a program for git to run (an alias, an editor, a "
                    "pager, a filter); the guard lets it set only `user.name` and `user.email`")
    return None


def git_global_problem(words):
    """(why a global option of the `git ...` command is not one the guard takes, or None; the index of the
    subcommand). It takes GIT_GLOBAL_FLAGS and `-C <dir>`: `-c`, `--config-env`, `--exec-path` and the like can name a
    program for git to run."""
    i = 1
    while i < len(words) and words[i].startswith("-"):
        if words[i] == "-C" and i + 1 < len(words):
            i += 2
        elif words[i] in GIT_GLOBAL_FLAGS:
            i += 1
        else:
            return (f"`{words[i]}` may not stand before the subcommand (only `-C <dir>`, `--no-pager`, `-P`, "
                    f"`--no-optional-locks`, `--literal-pathspecs`, `--no-replace-objects`)"), i
    return None, i


def git_read_problem(words):
    """Why the whole `git ...` command is not a known read, in words the caller can act on; None when it is one: no
    global option but the ones `git_global_problem` takes, then the subcommand, a listing word right after it when it
    has listing forms, and only that form's options and positional words (GIT_READS)."""
    problem, i = git_global_problem(words)
    if problem:
        return problem
    if i >= len(words):
        return None  # `git --version` and the like
    sub, args, forms = words[i], words[i + 1:], GIT_READS.get(words[i], {})
    if args and args[0] in forms:
        return forms[args[0]].problem(args[1:])
    if None in forms:
        return forms[None].problem(args)
    if forms:
        return f"`git {sub}` reads only as `git {sub} {'|'.join(forms)}`, with that word right after `{sub}`"
    return f"`git {sub}` is not a command that only reads"


def git_only_reads(words):
    """Whether the whole `git ...` command only reads (git_read_problem)."""
    return git_read_problem(words) is None


MESSAGE_COMMANDS = {"commit", "tag", "merge", "stash", "notes"}


def git_loose_words(sub, args):
    """The arguments of `git <sub>` that may name a path or an option: all but a message (`-m <text>`, `-am <text>`,
    `--message <text>`, `--message=<text>`), which git takes as text whatever it holds."""
    if sub not in MESSAGE_COMMANDS:
        return list(args)
    message = re.compile(r"-[A-Za-z]*m|--message")
    return [a for before, a in zip([""] + list(args), args)
            if not (message.fullmatch(before) or a.startswith("--message=") or (message.fullmatch(a) and not unresolved(a)))]


def git_named_paths(args):
    """What among `args` may be a path git writes to: a word or an option's value (`--output=<p>`, `-o<p>`) that
    holds a `/` or starts with `.` or `~`, a `file://` URL as its path. A short option may stand in a group
    (`-qo<p>`), so each tail of such a word that may be its value counts (`short_option_tails`), and so does what
    stands behind a `=` in it (`-c<key>=<p>`). A branch name or another URL resolves inside the repo, so it does no
    harm."""
    for a in args:
        if not a.startswith("-"):
            values = [a]
        elif a.startswith("--"):
            values = a.split("=", 1)[1:]
        else:
            values = short_option_tails(a) + a.split("=", 1)[1:]
        for v in values:
            v = v.removeprefix("file://")
            if "/" in v or v.startswith((".", "~")):
                yield v


def short_option_tails(word):
    """What may be the value attached in the short-option word `word` (`-o<p>`, `-qo<p>`): each tail with only
    option letters before it, since a value begins behind its option's letter and never in its own middle
    (`-m"fix a/b"` names no `/b`). Which letter takes the value the guard does not know, so `-bfeat/x` may also be
    `-t/x` behind four more letters: such a tail from the first `/` on counts only when it can be written
    (`/x` cannot, nor can `/users` or `/usr/bin`; `-qo/Users/<name>/...` can, as can every folder the guards keep).
    Right behind the first letter (`-o/abs/p`) a tail counts whatever it names."""
    letters = len(re.match(r"-[A-Za-z]*", word).group())
    tails = [word[k:] for k in range(2, min(letters, len(word) - 1) + 1)]
    first, further = tails[:1], tails[1:]
    return first + [t for t in further if not t.startswith("/") or can_be_written(t)]


def can_be_written(path):
    """Whether the user running the line can write at the absolute `path`: the path itself when it is there, else the
    nearest folder above it that is. The path is read twice, by its letters with `..` folded and with its links
    resolved, and either reading counts."""
    for place in {os.path.normpath(path), os.path.realpath(path)}:
        while not os.path.lexists(place):
            place = os.path.dirname(place)
        if os.access(place, os.W_OK):
            return True
    return False


def git_places(words, cwd):
    """Every directory the global options of a `git ...` command name: where `-C` leads (git_command), and each
    `--git-dir` and `--work-tree`, which may name two different repos."""
    sub, args, target = git_command(words, cwd)
    head = words[1:len(words) - len(args) - (sub is not None)]
    named = [t for w, t in zip(head, head[1:]) if w in ("--git-dir", "--work-tree")]
    named += [w.split("=", 1)[1] for w in head if w.startswith(("--git-dir=", "--work-tree="))]
    return [target] + [n if os.path.isabs(os.path.expanduser(n)) or cwd is None else str(Path(cwd) / n) for n in named]


# Builtins that evaluate an argument as arithmetic or as a name with a subscript, where the shell runs a `$( ... )`
# even from a quoted string: `read 'a[$(cmd)]'`, `test -v 'a[$(cmd)]'`, zsh's `[ 'a[$(cmd)]' -eq 1 ]`.
EVALUATORS = DECLARERS | {"printf", "test", "[", "wait"}


def evaluated_text(words):
    """A word of the simple command `words` (as written) that holds a quoted `$(` or backquote the shell may still
    run: the value of an assignment (used later as arithmetic) or an argument of a builtin that evaluates its
    arguments (EVALUATORS). None when there is none."""
    own = command_words(words)
    risky = words[:len(words) - len(own)] + (own[1:] if own and Path(own[0]).name in EVALUATORS else [])
    return next((w for w in risky if isinstance(w, Word) and not w.subs and re.search(r"\$\(|`", w)), None)
