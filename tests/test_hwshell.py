"""The shell parsing both guards rest on (bin/lib/hwshell.py), checked on its own: how a command line is cut into
simple commands and shells, where a `cd` leads, and what a command writes."""
import os
import tempfile
import unittest
from pathlib import Path

import _load  # noqa: F401  (puts bin/lib on sys.path)

import hwshell
from grammar_cases import READS, REFUSED, refused_lines
from hwshell import Unparsed, cd_target, command_words, redirect_targets, scoped_commands, simple_command, tokens, write_targets
from hwshell import loose_tokens

HOME = os.path.realpath(os.path.expanduser("~"))


class ScopeTests(unittest.TestCase):
    def scopes(self, command):
        return [(" ".join(words), scope) for words, scope in scoped_commands(loose_tokens(command))]

    def test_each_shell_of_a_command_line_has_its_own_scope(self):
        self.assertEqual(self.scopes("a && (b; (c)) | d & e"),
                         [("a", ()), ("b", (1,)), ("c", (1, 2)), ("d", (3,)), ("e", ())])
        self.assertEqual(self.scopes("a | b |& c; d"), [("a", (1,)), ("b", (2,)), ("c", (3,)), ("d", ())])
        self.assertEqual(self.scopes("a &\nb"), [("a", (1,)), ("b", ())])

    def test_a_new_scope_begins_where_its_parent_stands(self):
        dirs = {(): "/a"}
        self.assertEqual(hwshell.scope_dir(dirs, (1, 2)), "/a")
        dirs[(1,)] = "/b"
        self.assertEqual((hwshell.scope_dir(dirs, (1, 3)), hwshell.scope_dir(dirs, (1, 2)), hwshell.scope_dir(dirs, ())), ("/b", "/a", "/a"))

    def test_where_a_cd_leads(self):
        for command, cwd, target in (("cd /tmp", "/a", os.path.realpath("/tmp")), ("cd b", "/a", "/a/b"), ("cd -P -- b", "/a", "/a/b"),
                                     ("pushd b", "/a", "/a/b"), ("cd", "/a", HOME), ("cd b", None, None), ("cd -", "/a", None),
                                     ("cd $X", "/a", None), ("popd", "/a", None), ("pushd", "/a", None),
                                     ("cd a b", "/x/a", None)):  # zsh: `a` in the current directory's path becomes `b`
            self.assertEqual(cd_target(command.split(), cwd), target, command)


class WordTests(unittest.TestCase):
    def test_a_commands_own_words_begin_with_its_program(self):
        for command, own in (("FOO=1 BAR=2 touch x", "touch x"), ("then touch x", "touch x"), ("do", ""), ("! rm x", "rm x"),
                             ("{ env -u A B=1 touch x", "touch x"), ("sudo -u bob nice -n 5 rm x", "rm x"), ("xargs -n 1 rm", "rm"),
                             ("/usr/bin/env touch x", "touch x"), ("ls -l", "ls -l"), ("exec -a name time rm x", "rm x"),
                             ("timeout 5 rm x", "rm x"), ("timeout -s KILL -k 1 5 rm x", "rm x"), ("timeout --signal=KILL 5s rm x", "rm x"),
                             ("gtimeout 5 rm x", "rm x"), ("env A=1 rm x", "rm x"), ("nohup rm x", "rm x"), ("time -p rm x", "rm x"),
                             ("xargs rm", "rm"), ("sudo rm x", "rm x"), ("command rm x", "rm x"), ("exec rm x", "rm x"),
                             ("stdbuf -oL rm x", "rm x"), ("stdbuf -o L -e 0 rm x", "rm x"), ("setsid rm x", "rm x"),
                             ("caffeinate -i -t 60 rm x", "rm x"), ("nohup timeout 5 nice rm x", "rm x"), ("timeout 5", "")):
            self.assertEqual(" ".join(command_words(command.split())), own, command)

    def test_what_a_command_writes(self):
        for command, redirects, writes in (
                ("echo x > a 2>&1", ["a"], []), ("echo x &> a", ["a"], []), ("echo x >& a >&2", ["a"], []), ("echo x >> a >| b &>> c", ["a", "b", "c"], []),
                ("rm -rf a b", [], ["a", "b"]), ("install -m 755 a b", [], ["755", "a", "b"]), ("rsync -a a b", [], ["a", "b"]), ("chmod -R u+x , g-w a", [], ["a"]), ("chmod 755 a b", [], ["a", "b"]), ("chown bob:staff a", [], ["a"]),
                ("sed -i.bak s/a/b/ f", [], ["s/a/b/", "f"]), ("sed s/a/b/ f", [], []), ("dd if=a of=b bs=1", [], ["b"]),
                ("find a b -name x -delete", [], ["a", "b"]), ("find -delete", [], ["."]),
                ("find -L a -name x -delete", [], ["a"]), ("find -H -O2 -D tree a b -delete", [], ["a", "b"]), ("find -P -delete", [], ["."]),
                ("find -E -X -d -s -x a -delete", [], ["a"]), ("find -f a -E b -delete", [], ["a", "b"]),  # BSD's (macOS)
                # grouped (BSD), an attached word, `--`; a primary that looks like a group is not one
                ("find -EX a -delete", [], ["a"]), ("find -EXdsx a -delete", [], ["a"]), ("find -Ef a b -delete", [], ["a", "b"]),
                ("find -Xfa b -delete", [], ["a", ".", "b"]), ("find -Dtree a -delete", [], ["a"]), ("find -L -- a -delete", [], ["a"]),
                ("find -follow -delete", [], ["ollow", "."]), ("find -depth -delete", [], ["."]), ("find -x -delete", [], ["."]),
                ("find a -exec rm {}", [], ["a"]),
                ("find a -exec grep x {}", [], []), ("ls a", [], []),
                # a path as an option's value
                ("mktemp --tmpdir=a", [], ["a"]), ("cp --target-directory=a -v b", [], ["b", "a"]), ("cp -t/a/b -v c", [], ["c", "/a/b"]),
                ("mv -t.. c", [], ["c", ".."]), ("rm -rf a", [], ["a"])):
            words = command.split()
            self.assertEqual((redirect_targets(words), write_targets(words)), (redirects, writes), command)

    def test_a_run_of_operators_is_cut_into_each(self):
        for command, words in (("true;>a", ["true", ";", ">", "a"]), ("(a);b", ["(", "a", ")", ";", "b"]), ("a&>>b", ["a", "&>>", "b"]),
                               ("a|&b", ["a", "|&", "b"]), ("a;>|b", ["a", ";", ">|", "b"]), ("a 2>&1", ["a", "2", ">&", "1"]),
                               ("a&&>b", ["a", "&&", ">", "b"]), ("a ;; b", ["a", ";", "b"]), ("a<<<b", ["a", "<<<", "b"]),
                               ("a<>b", ["a", "<>", "b"]), ("a ;& b ;;& c", ["a", ";", "b", ";", "c"])):
            self.assertEqual(loose_tokens(command), words, command)

    def test_a_heredoc_keeps_the_rest_of_its_opening_line(self):
        self.assertEqual(loose_tokens("cat <<EOF > a\nrm b\nEOF\nls"), ["cat", ">", "a", ";", "ls"])
        self.assertEqual(loose_tokens("cat <<-EOF\n\tx\n\tEOF\nls"), ["cat", ";", "ls"])
        self.assertEqual(loose_tokens("cat <<EOF\n  EOF\nEOF\nls"), ["cat", ";", "EOF", ";", "ls"])  # read loosely, an indented end line ends it too
        self.assertEqual(loose_tokens("cat > a <<'EOF'\nrm b\nEOF"), ["cat", ">", "a", ";"])
        self.assertEqual(loose_tokens("cat <<A <<B > c\n1\nA\n2\nB"), ["cat", ">", "c", ";"])

    def test_a_command_line_is_read_loosely_as_the_shell_reads_it(self):
        for command, words in (("a \\\n b", ["a", "b"]), ("a\\\nb", ["ab"]), ("a 'b\\\nc'", ["a", "b\\\nc"]), ('a "b\\\nc"', ["a", "bc"]),
                               ("a\nb", ["a", ";", "b"]), ("a 'b\nc'", ["a", "b\nc"]), ("a#b c # d\ne", ["a#b", "c", ";", "e"]),
                               ("a ';' \">\" \\| b", ["a", ";", ">", "|", "b"]), ("a 'b c'\"d e\"f\\ g", ["a", "b cd ef g"]),
                               ("a '' \"\"", ["a", "", ""]), ('a "b\\"c\\$d"', ["a", 'b"c$d']), ("u+x,g-w a,b", ["u+x,g-w", "a,b"]),
                               ("a{b,c}d", ["abd", "acd"]), ("{a,b}{c,d}", ["ac", "ad", "bc", "bd"]), ("a{b,{c,d}}", ["ab", "ac", "ad"]),
                               ("'{a,b}' {} {a} HEAD@{1}", ["{a,b}", "{}", "{a}", "HEAD@{1}"]), ("f() { a; }", ["f", "(", ")", "{", "a", ";", "}"])):
            self.assertEqual(loose_tokens(command), words, command)
        operators = [type(t) is str for t in loose_tokens("a ';' ; b")]
        self.assertEqual(operators, [False, False, True, False])  # a quoted `;` is a word, never an operator
        self.assertEqual(redirect_targets(loose_tokens("echo '>' a > b")), ["b"])

    def test_a_word_knows_what_only_the_shell_can_tell(self):
        for command, unknown, glob in (("$X", True, False), ('"$X/a"', True, False), ("'$X'", False, False), ("\\$X", False, False),
                                       ("${X:-a}", True, False), ("$((1+2))", True, False), ("$(ls)", True, False), ("~bob/a", True, False),
                                       ("~/a", False, False), ("a{1..3}", True, False), ("a*", False, True), ("'a*'", False, False),
                                       ("a?[b]", False, True), ("[", False, False), ("a$", False, False), ("$1$@$?", True, False)):
            word = loose_tokens(command)[0]
            self.assertEqual((word.unknown, word.glob), (unknown, glob), command)
            self.assertEqual(hwshell.unresolved(word), unknown, command)
        self.assertTrue(hwshell.unresolved("$X") and hwshell.unresolved("`a`") and not hwshell.unresolved("a"))  # a plain string: by its characters

    def test_a_command_substitution_is_read_as_commands_of_its_own(self):
        self.assertEqual(self.scopes('a "$(b; c $(d))" e'), [("b", (1,)), ("d", (1, 1)), ("c $(...)", (1,)), ("a $(...) e", ())])
        self.assertEqual(self.scopes('a "$(cat <<\'EOF\'\n) "\' $(x)\nEOF\n)" b'), [("cat", (1,)), ("a $(...) b", ())])
        self.assertEqual(self.scopes("(a $(b)) | c"), [("b", (1, 2)), ("a $(...)", (1,)), ("c", (3,))])

    scopes = ScopeTests.scopes

    def test_read_loosely_only_what_is_unclosed_raises(self):
        for command in ("a 'b", 'a "b', "a $(b", "a ${b", "a $((b"):
            self.assertRaises(Unparsed, loose_tokens, command)
        for command, words in (("a `b`", ["a", "`b`"]), ("a <(b)", ["a", "<", "(", "b", ")"]), ("a=(b c)", ["a=", "(", "b", "c", ")"]),
                               ("a $'b'", ["a", "b"]), ("a >! b", ["a", ">", "!", "b"]), ("a <<EOF\nb", ["a", ";"]),
                               ("a <<EOF\n$(b)\nEOF", ["a", ";"]), ("a <<EOF\nb\n EOF ", ["a", ";"]), ("a *(.)", ["a", "*", "(", ".", ")"]),
                               ("a $(( $(b) ))", ["a", "$(( $(b) ))"]), ("a ${b:-$(c)}", ["a", "${b:-$(c)}"])):
            self.assertEqual(loose_tokens(command), words, command)
        self.assertTrue(loose_tokens("a `b`")[1].unknown)

    def test_which_variable_a_command_names_where_the_shell_could_set_it(self):
        for command, name in (("GIT_DIR=x git a", "GIT_DIR"), ("env A=1 GIT_DIR=x git a", "GIT_DIR"), ("export A=1 PAGER", "PAGER"),
                              ("typeset -x EDITOR=x", "EDITOR"), ("read -r a HOME", "HOME"), ("printf -v CDPATH %s a", "CDPATH"),
                              ("printf -vGIT_DIR %s a", "GIT_DIR"), ("print -v GIT_DIR x", "GIT_DIR"), ("zstyle -s :x y GIT_DIR", "GIT_DIR"),
                              ("declare -n r=GIT_DIR", "GIT_DIR"), ("GIT_DIR+=x a", "GIT_DIR"), ("env -u PATH a", "PATH"), ("unset HOME", "HOME"),
                              ("some-builtin TMPDIR", "TMPDIR"), ("path+=/x", "path"), ("typeset fpath=/x", "fpath"),
                              # only text: an assignment to another name, a program that sets nothing, reading it
                              ("A=1 b", None), ("export A=$B", None), ("echo GIT_DIR=x", None), ("grep -rn GIT_DIR bin", None),
                              ("git log --grep=PATH", None), ("bin/hw log T1 PATH", None), ("printf %s $PATH", None),
                              ("some-builtin ${HOME}/x", None), ("FOO=$PATH b", None), ("ls path=x", None), ("unset MY_GIT_DIR_X", None)):
            self.assertEqual(hwshell.guarded_variable(tokens(command)), name, command)


def read(line):
    """`line` read by the grammar: the (own words, words before, files written) of each of its simple commands."""
    return [simple_command(raw, piped=scope != ()) for raw, scope in scoped_commands(tokens(line))]


class GrammarTests(unittest.TestCase):
    """The grammar the coordinator's guard and a read-only hat's guard read (hwshell.GRAMMAR): one case or more per
    rule, and every construct outside it."""

    def test_what_the_grammar_reads(self):
        for line, words in READS:
            with self.subTest(line):
                self.assertEqual(tokens(line), words)
                read(line)  # ... and each of its commands is one the grammar takes

    def test_an_operator_is_never_a_quoted_word_and_a_redirect_leaves_its_file(self):
        self.assertEqual([type(t) is str for t in tokens("a ';' ; b '>' > c")], [False, False, True, False, False, True, False])
        self.assertEqual(read("FOO=1 env -u A nice -n 5 touch x > /tmp/y 2>&1 < z"),
                         [(["touch", "x"], ["FOO=1", "env", "-u", "A", "nice", "-n", "5"], ["/tmp/y"])])
        self.assertEqual(read("> a"), [([], [], ["a"])])

    def test_a_word_knows_what_the_shell_would_expand(self):
        for word, unknown, glob, extended in (("$X", True, False, False), ('"${X}/a"', True, False, False), ("'$X'", False, False, False),
                                              ("\\$X", False, False, False), ("a*", False, True, False), ("'a*'", False, False, False),
                                              ("a?[b]", False, True, False), ("[", False, False, False), ("*", False, True, False),
                                              ("HEAD~1", False, False, True), ("HEAD^", False, False, True), ("a#b", False, False, True),
                                              ("~/a", False, False, False), ("of=~/a", True, False, True), ("{}", False, False, False),
                                              ("main@{1}", False, False, False)):
            w = tokens(f"echo {word}")[1]
            self.assertEqual((w.unknown, w.glob, w.extended), (unknown, glob, extended), word)

    def test_every_construct_outside_the_grammar_is_refused_and_named(self):
        for construct, piece in REFUSED:
            for write in ("true", "touch /code/proj/x.txt"):
                line = construct.format(w=write)
                with self.subTest(line):
                    with self.assertRaises(Unparsed) as refusal:
                        read(line)
                    self.assertIn(piece, str(refusal.exception))

    def test_no_line_built_from_a_refused_construct_is_read(self):
        lines = list(refused_lines())
        self.assertGreater(len(lines), 1500)
        for line in lines:
            with self.subTest(line):
                self.assertRaises(Unparsed, read, line)

    def test_a_wrapper_takes_only_the_options_the_guard_knows(self):
        """An option nobody listed could take the next word as its value and so hide the program: `env -P <dir> touch x`."""
        for line, own in (("env -P /usr/bin touch x", "touch x"), ("env -i -u A B=1 touch x", "touch x"), ("nice -n 5 touch x", "touch x"),
                          ("nice -5 touch x", "touch x"), ("command -p touch x", "touch x"), ("exec -a n touch x", "touch x"),
                          ("timeout -s KILL -k 1 5 touch x", "touch x"), ("timeout --signal=KILL 5s touch x", "touch x"),
                          ("stdbuf -oL touch x", "touch x"), ("caffeinate -i -t 60 touch x", "touch x"), ("arch -arm64 touch x", "touch x"),
                          ("arch -arch arm64 touch x", "touch x"), ("xcrun --sdk macosx touch x", "touch x"), ("sudo -n -u bob touch x", "touch x"),
                          ("time -p touch x", "touch x"), ("env -- touch x", "touch x"), ("nohup nice touch x", "touch x"),
                          ("watch -d -n 5 touch x", "touch x")):
            self.assertEqual(" ".join(command_words(tokens(line), strict=True)), own, line)
        for line in ("env -S 'touch x'", "env -C /a touch x", "env -x touch x", "sudo -r role touch x", "nice --adjustment 5 touch x",
                     "timeout --kill-after 1 5 touch x", "arch -foo touch x", "command -x touch x", "xcrun -find touch"):
            self.assertRaises(Unparsed, command_words, tokens(line), True)
            command_words(tokens(line))  # read loosely, for a hat that edits, it is skipped as before

    def test_the_wrappers_added_in_round_7_take_their_exact_options_and_a_lock_file_is_a_file_they_write(self):
        for line, own, files in (("taskpolicy -b -c utility touch x", "touch x", []), ("lockf -k -t 5 /tmp/l touch x", "touch x", ["/tmp/l"]),
                                 ("flock -n -w 5 /tmp/l touch x", "touch x", ["/tmp/l"]), ("chronic -ev touch x", "touch x", []),
                                 ("sandbox-exec -n no-network -D A=1 touch x", "touch x", []), ("nice lockf l env flock m touch x", "touch x", ["l", "m"])):
            written = []
            self.assertEqual((" ".join(command_words(tokens(line), strict=True, files=written)), written), (own, files), line)
            self.assertEqual(read(line)[0][2], files, line)
        for line in ("taskpolicy -p 1 touch x", "lockf -x l touch x", "flock -c 'touch x' l", "sandbox-exec -x touch x", "chronic --x touch x"):
            self.assertRaises(Unparsed, command_words, tokens(line), True)

    def test_a_sed_script_that_may_write_a_file_or_run_a_program_is_refused_and_the_plain_forms_pass(self):
        for script in ("10,20p", "s/a/b/g; s/c/d/", "/^## R/,$p", "1!G;h;$!d", "$!N;s/\\n//;ta", "5,+2d", "1~2d", "y/abc/xyz/",
                       # a `w` or an `e` that is text of `s///` or of an address, in a form the guard reads in full
                       "s/wip/done/", "/warn/p", "s|we|they|g;2,5d", "/review/,/verdict/p", "$!d;s/ were / was /2p", "/a\\/w f/!p",
                       "s/\\[we\\]/x/", "2q;s#w#e#", "s/x/y/\n/w/d"):
            self.assertIsNone(hwshell.sed_problem(["sed", script, "f"]), script)
        for script in ("w out.txt", "W  o", "s/x/y/w /r/f", "s|a|b|gw out", "/x/w a\n$w b", "p;w o;p", "1e ls", "s/a/b/e", "e",
                       # text of `a`, `i`, `c` that ends in an escaped backslash; a delimiter inside brackets
                       "a\\\nhello\\\\\nw f2", "1i\\\nx\\\\\nw f", "s/[/]/g;/w x.txt", "s/[/]/;s/w f/x/",
                       # an `s` that is no command where it stands would hide the `w` behind it
                       "/s#/p;w f#x#", "s/a/b/;w f", "{w f\n}", "a\\\nwe", "s/[a-z]*e//", "r new.txt"):
            self.assertIn("holds a `w` or an `e`", hwshell.sed_problem(["sed", script, "f"]), script)
        for line, refused in (("sed -n 10,20p f", False), ("sed -ne p -e 2q f", False), ("sed -E --expression=p -- f", False), ("sed", False),
                              ("sed -n 'w /x' a", True), ("sed -ne p -e 'w o' f", True), ("sed -E --expression='w o' f", True),
                              ("sed --quiet -e 'w o' -- f", True), ("sed -i.bak 's/a/b/w o' f", True), ("sed -i '' 's/a/b/w o' f", True),
                              ("sed -i 's/a/b/w o' p", True), ("sed -i p 'w o' f", True), ("sed -f s.sed f", True), ("sed -x p f", True),
                              ("sed --file=s f", True), ("sed -e p -e 1e f", True),
                              ("sed -l 1 'w /x' f", True), ("sed -nl 1 'w /x' f", True)):  # GNU's `-l <N>` takes the next word
            self.assertEqual(hwshell.sed_problem(tokens(line)) is not None, refused, line)
            if refused:
                self.assertRaises(Unparsed, read, line)
        self.assertEqual(hwshell.write_targets(tokens("sed -i.bak s/a/b/ f")), ["s/a/b/", "f"])
        self.assertEqual(hwshell.write_targets(tokens("sed -n p f")), [])

    def test_a_link_writes_itself_and_what_it_points_to_read_from_the_folder_that_holds_it(self):
        for line, targets in (("ln -s ../../repo d/l", ["d/l", "d/l/../../repo", "d/../../repo"]), ("ln -s /abs l", ["l", "/abs"]),
                              ("ln -s a", [".", "./a"]), ("ln -sfn ../x sub/l", ["sub/l", "sub/l/../x", "sub/../x"]),
                              ("ln a b", ["b", "a"]), ("ln ../a b", ["b", "../a"]),  # a hard link: the file is named from where the shell stands
                              ("ln -s a b d", ["d", "d/a", "./a", "d/b", "./b"]), ("ln -s -t d ../a", ["d", "d/../a"]),
                              ("ln --symbolic --target-directory=d ../a", ["d", "d/../a"]), ("ln -s -td ../a", ["d", "d/../a"])):
            self.assertEqual(hwshell.write_targets(tokens(line)), targets, line)

    def test_of_a_copy_only_the_target_is_written(self):
        for line, targets in (("cp src/b.py /out/x", ["/out/x"]), ("cp -R a b c", ["c"]), ("cp a", ["a"]), ("cp -t d a b", ["d", "a", "b"]),
                              ("cp --target-directory=d a", ["a", "d"]), ("mv a b", ["a", "b"])):
            self.assertEqual(hwshell.write_targets(tokens(line)), targets, line)

    def test_a_copy_that_links_writes_its_sources_too(self):
        """`cp -l` makes a hard link and `cp -s` a symbolic one, so a later write to the "copy" changes the source."""
        for line in ("cp -l a b", "cp -al a b", "cp -s a b", "cp -Rs a b", "cp --link a b", "cp --symbolic-link a b",
                     "cp --li a b", "cp --sym a b", "cp --symbolic a b"):  # GNU cp takes a long option by its beginning
            self.assertEqual(sorted(hwshell.write_targets(tokens(line))), ["a", "b"], line)
        for line in ("cp -RL a b", "cp --strip-trailing-slashes a b", "cp --recursive a b"):
            self.assertEqual(hwshell.write_targets(tokens(line)), ["b"], line)
        self.assertEqual(hwshell.write_targets(tokens("cp --target-dir=d a")), ["a", "d"])

    def test_a_path_git_is_given_is_found_behind_a_group_of_short_options(self):
        """`-qo/abs/p` read from its third letter gave `o/abs/p`, which looks relative."""
        with tempfile.TemporaryDirectory() as root:  # a folder that is there: see the next test
            for word, path in ((f"-qo{root}/p", f"{root}/p"), ("-ko../../repo/p", "../../repo/p"), (f"-zf{root}/.git/config", f"{root}/.git/config"),
                               (f"-kqo{root}/p", f"{root}/p"), ("-o/abs/p", "/abs/p"), ("--output=/abs/p", "/abs/p"), ("-qo~/p", "~/p"),
                               (f"-o{root}/a=b", f"{root}/a=b"), (f"-qo{root}/a=b", f"{root}/a=b"),  # a `=` in the path hid what stands before it
                               ("-ckey=/abs/p", "/abs/p")):
                self.assertIn(path, list(hwshell.git_named_paths([word])), word)
        self.assertEqual(list(hwshell.git_named_paths(["-q", "-1", "main", "--stat"])), [])

    def test_a_value_attached_to_a_short_option_names_no_path_from_its_middle(self):
        """Every tail of a short-option word counted, so `-m"fix a/b"` named `/b` and `-bfeat/x` named `/x`,
        paths outside every folder, and a commit message or a branch name with a `/` was refused."""
        outside = lambda word: [p for p in hwshell.git_named_paths([word]) if p.startswith(("/", "~")) or ".." in p]
        for word in ("-mfix a/b", "-amfix a/b", "-msee README, 1/2", "-bfeat/x", "-ctask/x", "-mwip a/b", "-opatches/sub",
                     "-qopatches/sub", "-bfix/no-such-folder-here/x"):
            self.assertEqual(outside(word), [], word)
        self.assertEqual(outside("-o/abs/p"), ["/abs/p"])  # right after the option's letter it is the value, whatever it names
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(outside(f"-F{root}/f.txt"), [f"{root}/f.txt"])
            self.assertEqual(outside(f"-qo{root}/a/b"), [f"{root}/a/b"])  # one path, not `/a/b` and `/b` as well
            self.assertIn(f"/no-such-folder-here/..{root}/p", outside(f"-qo/no-such-folder-here/..{root}/p"))  # read by its letters, `..` folded
            self.assertEqual(outside(f"-bfix{root}/new/x"), [f"{root}/new/x"])  # not there yet, in a folder that can be written

    @unittest.skipIf(os.geteuid() == 0, "root can write everywhere")
    def test_a_value_attached_to_a_short_option_names_no_path_nobody_can_write(self):
        """`-bfeature/users` and `-m"docs/var"` were refused, since `/users` and `/var` are there. A tail
        further in is a path only where the user running the line can write: every folder the guards keep is one."""
        outside = lambda word: [p for p in hwshell.git_named_paths([word]) if p.startswith("/")]
        with tempfile.TemporaryDirectory() as root:
            kept = Path(root, "kept")
            kept.mkdir()
            Path(kept, "file").write_text("")
            Path(kept, "sub").mkdir()
            kept.chmod(0o555)
            try:
                for word in (f"-bfix{kept}", f"-bfix{kept}/x", f"-mdocs{kept}/no/such", f"-qo{kept}/../kept/x"):
                    self.assertEqual(outside(word), [], word)
                for path in (f"{kept}/file", f"{kept}/sub", f"{kept}/sub/x", f"{kept}/../x"):  # what can be written there, and beside it
                    self.assertEqual(outside(f"-qo{path}"), [path])
                self.assertEqual(outside(f"-o{kept}/x"), [f"{kept}/x"])  # right after the option's letter, whatever it names
            finally:
                kept.chmod(0o755)

    def test_an_option_that_names_a_program_for_git_is_known_by_any_beginning_git_takes(self):
        """Git takes a long option by any unambiguous beginning (`--exe`, `--upload-pa`) and a short one with
        its value attached (`-x<cmd>`); only the full spellings were known."""
        for sub, args in (("rebase", ["--exe=make", "HEAD~1"]), ("rebase", ["--exe", "make", "HEAD~1"]), ("rebase", ["--ex=make"]),
                          ("rebase", ["-xmake", "HEAD~1"]), ("rebase", ["-x", "make"]),
                          ("rebase", ["-qxmake", "HEAD~1"]), ("clone", ["-qutouch", ".", "c"]),  # inside a group of short options
                          ("fetch", ["-q", "--upload-pa=x", "origin"]),
                          ("pull", ["--upl=x"]), ("ls-remote", ["--exe=x", "origin"]), ("grep", ["--open-files-in-p=x", "x"]),
                          ("push", ["--rec=x"]), ("difftool", ["--ext=x"]), ("clone", ["-utouch", ".", "c"]), ("ls-remote", ["--upload-pack=x"])):
            self.assertIsNotNone(hwshell.git_runs_a_program(sub, args), (sub, args))
        for sub, args in (("log", ["--exclude=x", "--all"]), ("fetch", ["--update-head-ok"]), ("rebase", ["--onto", "x", "-Xours"]),
                          ("commit", ["--reuse-message=HEAD"]), ("clone", ["-q", ".", "c"]), ("rebase", ["-q", "--onto=x/y", "main"]), ("status", ["--exec-path-is-no-option"])):
            self.assertIsNone(hwshell.git_runs_a_program(sub, args), (sub, args))

    def test_the_data_volume_s_second_name_is_the_same_folder(self):
        """macOS shows the data volume a second time under /System/Volumes/Data, which is no link."""
        with tempfile.TemporaryDirectory() as root:
            root = os.path.realpath(root)
            self.assertEqual(hwshell.first_name(f"{root}/new/x.py"), f"{root}/new/x.py")
            if os.path.isdir(hwshell.DATA_VOLUME + root):
                self.assertEqual(hwshell.first_name(f"{hwshell.DATA_VOLUME}{root}/new/x.py"), f"{root}/new/x.py")
        self.assertEqual(hwshell.first_name(hwshell.DATA_VOLUME + "/no-such-folder-here/x"), hwshell.DATA_VOLUME + "/no-such-folder-here/x")

    def test_a_cd_is_followed_only_before_and_and_into_a_folder_that_is_there_as_the_shell_walks_it(self):
        with tempfile.TemporaryDirectory() as root:
            root = os.path.realpath(root)
            os.makedirs(f"{root}/sub/deep")
            Path(root, "afile").write_text("x")
            os.symlink("sub/deep", f"{root}/link")
            self.assertEqual(hwshell.cd_followed(["cd", f"{root}/sub"], "/", "&&"), f"{root}/sub")
            self.assertEqual(hwshell.cd_followed(["cd", "sub"], root, "&&"), f"{root}/sub")
            self.assertEqual(hwshell.cd_followed(["cd", "./link"], root, "&&"), f"{root}/sub/deep")
            for then in (";", "||", "|", None):  # what follows runs whether or not the `cd` succeeded
                self.assertIsNone(hwshell.cd_followed(["cd", f"{root}/sub"], "/", then), then)
            # asked of the path as written: a `..` after a folder that is not there, after a file, after a link
            for target in (f"{root}/nope", f"{root}/afile", f"{root}/sub/x", "nope", f"{root}/nope/..", f"{root}/nope/../sub",
                           f"{root}/afile/../sub", "nope/../sub", f"{root}/link/..", "link/../sub", "sub/..", ".."):
                self.assertIsNone(hwshell.cd_followed(["cd", target], root, "&&"), target)
            self.assertIsNone(hwshell.cd_followed(["cd", "sub"], None, "&&"))

    def test_the_line_s_shell_is_followed_from_its_head_to_the_first_command_that_runs_either_way(self):
        with tempfile.TemporaryDirectory() as root:
            root = os.path.realpath(root)
            os.makedirs(f"{root}/sub/deep")
            sub, deep = f"{root}/sub", f"{root}/sub/deep"
            for line, stands in (("ls; ls | cat", [root, root, root]), ("cd sub && ls && ls | cat", [root, sub, sub, sub]),
                                 ("cd sub && cd deep && ls", [root, sub, deep]), ("cd sub && ls; ls", [root, sub, None]),
                                 ("cd sub && ls || ls", [root, sub, None]), ("cd sub && ls\nls", [root, sub, None]),
                                 ("cd sub; ls", [root, None]), ("cd sub || ls", [root, None]), ("cd sub\nls", [root, None]),
                                 ("cd nope && ls", [root, None]), ("cd sub/.. && ls", [root, None]),
                                 ("ls && cd sub && ls", [root, root, None]), ("mkdir -p new; cd new && ls", [root, root, None]),
                                 ("cd nope; cd sub && ls", [root, None, None]), ("cd sub && ls; cd deep && ls | cat", [root, sub, None, None, None]),
                                 ("X=1 && cd sub && ls", [root, root, None]), ("CDPATH=/x; cd sub && ls", [root, root, None])):
                standing, stood = hwshell.Standing(root), []
                for raw, scope in scoped_commands(tokens(line)):
                    stood.append(standing.at(scope))
                    standing.after(simple_command(raw, piped=scope != ())[0], scope, raw.then)
                self.assertEqual(stood, stands, line)
            loose = hwshell.Standing(root, strict=False)  # a hat that edits: `cd_target`
            loose.after(["cd", "nope"], (), ";")
            self.assertEqual(loose.at(()), f"{root}/nope")

    def test_the_paths_that_may_lead_elsewhere_after_a_command(self):
        root = "/r"
        for words in (["mkdir", "-p", "sub"], ["touch", "sub/x"], ["tee", "x"], ["ls", "sub"], ["rm", "$X"], [], ["rm", "-rf", "sub"],
                      ["chmod", "000", "sub"]):
            self.assertEqual(hwshell.changed_paths(words, root), set(), words)
        self.assertEqual(hwshell.changed_paths(["mv", "x", "y"], None), set())
        for words, moved in ((["mv", "sub", "s2"], {"/r/sub", "/r/s2"}), (["ln", "-sfn", "/x", "sub"], {"/x", "/r/sub"}),
                             (["cp", "-R", "sub", "t"], {"/r/t"})):
            self.assertEqual(hwshell.changed_paths(words, root), moved, words)

    def test_paths_are_compared_without_regard_to_case_where_the_disk_does_not_tell_the_cases_apart(self):
        with tempfile.TemporaryDirectory() as root:
            root = os.path.realpath(root)
            os.makedirs(f"{root}/wt/src")
            blind = os.path.exists(f"{root}/WT")  # a default macOS disk
            self.assertEqual(hwshell.case_blind(f"{root}/wt/src/new.txt"), blind)
            self.assertEqual(hwshell.inside(f"{root}/WT/src/a.py", [Path(root, "wt")], "/"), blind)
            self.assertEqual(hwshell.inside("SRC/a.py", [Path(root, "wt")], f"{root}/Wt"), blind)
            self.assertTrue(hwshell.inside(f"{root}/wt/src/a.py", [Path(root, "wt")], "/"))
            self.assertFalse(hwshell.inside(f"{root}/other", [Path(root, "wt")], "/"))

    def test_a_command_knows_the_operator_that_follows_it(self):
        self.assertEqual([c.then for c, _ in scoped_commands(tokens("a && b | c || d; e\nf"))], ["&&", "|", "||", ";", ";", None])

    def test_a_path_walks_through_what_the_line_changed_as_the_shell_walks_it(self):
        changed = hwshell.changed_paths(["mv", "/x", "/a/d/k"], "/")
        for path, at, through in (("/a/d/k/../x", True, True), ("/a/d/k/x", False, True), ("/a/d/k", True, True), ("/a/d/k", False, False),
                                  ("/a/d/x", True, False), ("/a/d/../d/k/x", True, True), ("/b", True, False)):
            self.assertEqual(hwshell.walks_through(path, "/", changed, at), through, (path, at))
        self.assertTrue(hwshell.walks_through("k/../x", "/a/d", changed))
        self.assertFalse(hwshell.walks_through("k/../x", None, changed))
        self.assertFalse(hwshell.walks_through("/a/d/k/x", "/", set()))

    def test_a_guarded_name_is_found_by_the_set_of_names_asked_for(self):
        for line, every, git in (("GIT_DIR=x git log", "GIT_DIR", "GIT_DIR"), ("PATH=x GIT_DIR=y git log", "PATH", "GIT_DIR"), ("PATH=x ls", "PATH", None),
                                 ("export GIT_WORK_TREE", "GIT_WORK_TREE", "GIT_WORK_TREE"), ("git log --grep=GIT_DIR", None, None), ("path+=x", "path", None)):
            words = tokens(line)
            self.assertEqual((hwshell.guarded_variable(words), hwshell.guarded_variable(words, hwshell.NAMES_GIT)), (every, git), line)


if __name__ == "__main__":
    unittest.main()
