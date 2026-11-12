"""The shell parsing both guards rest on (bin/lib/hwshell.py), checked on its own: how a command line is cut into
simple commands and shells, where a `cd` leads, and what a command writes."""
import os
import unittest

import _load  # noqa: F401  (puts bin/lib on sys.path)

import hwshell
from hwshell import cd_target, command_words, redirect_targets, scoped_commands, tokens, write_targets

HOME = os.path.realpath(os.path.expanduser("~"))


class ScopeTests(unittest.TestCase):
    def scopes(self, command):
        return [(" ".join(words), scope) for words, scope in scoped_commands(tokens(command))]

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
                                     ("cd $X", "/a", None), ("popd", "/a", None), ("pushd", "/a", None)):
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
                ("find a -exec rm {}", [], ["a"]),
                ("find a -exec grep x {}", [], []), ("ls a", [], [])):
            words = command.split()
            self.assertEqual((redirect_targets(words), write_targets(words)), (redirects, writes), command)

    def test_a_run_of_operators_is_cut_into_each(self):
        for command, words in (("true;>a", ["true", ";", ">", "a"]), ("(a);b", ["(", "a", ")", ";", "b"]), ("a&>>b", ["a", "&>>", "b"]),
                               ("a|&b", ["a", "|&", "b"]), ("a;>|b", ["a", ";", ">|", "b"]), ("a 2>&1", ["a", "2", ">&", "1"]),
                               ("a&&>b", ["a", "&&", ">", "b"]), ("a ;; b", ["a", ";;", "b"]), ("a<<<b", ["a", "<<<", "b"])):
            self.assertEqual(tokens(command), words, command)

    def test_a_heredoc_keeps_the_rest_of_its_opening_line(self):
        self.assertEqual(tokens("cat <<EOF > a\nrm b\nEOF\nls"), ["cat", ">", "a", ";", ";", "ls"])
        self.assertEqual(tokens("cat > a <<'EOF'\nrm b\nEOF"), ["cat", ">", "a", ";"])
        self.assertEqual(tokens("cat <<A <<B > c\n1\nA\n2\nB"), ["cat", ">", "c", ";"])


if __name__ == "__main__":
    unittest.main()
