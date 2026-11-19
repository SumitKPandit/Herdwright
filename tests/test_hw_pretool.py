import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import _load

import hwdeny
import hwshell
from grammar_cases import ATTACHED_VALUES, PROJECT_WRITES, REFUSED, form_lines, refused_lines
from probe_git_reads import PROGRAM, probe_repo, repo_state, run_git
from real_shell import Scratch

pt = _load.load("hw-pretool")
HW, PROFILE = Path("/hw-home"), Path("/hw-home/profile")
ROOTS = [HW, PROFILE, Path("/tmp"), Path("/private/tmp")]
COORD = "sess-1"
PROTECTED = [Path("/tmp/scratch/demo-repo"), Path("/tmp/scratch/worktrees")]  # the user's projects (projects.json)
CAN_ENTER = hwshell.can_enter  # the real check; the tests on the tree above, which is not on disk, replace it (setUp)


def decide(tool, cwd="/hw-home", holder=COORD, sid=COORD, **inp):
    return pt.decide({"session_id": sid, "tool_name": tool, "tool_input": inp, "cwd": cwd}, holder=holder, roots=ROOTS,
                     protected=PROTECTED)


def bash(cmd, **kw):
    return decide("Bash", command=cmd, **kw)


READS = ["status", "status --porcelain=v2 -uno", "log --oneline -5", "log -n5 --format=%h", "diff main...HEAD",
         "diff --stat --cached", "show HEAD:README.md", "rev-parse --abbrev-ref HEAD", "ls-files", "blame a.py", "grep -n x",
         "for-each-ref refs/heads", "for-each-ref '--format=%(refname)' --sort=-committerdate", "merge-base main HEAD",
         "ls-remote origin", "describe --tags --always", "--no-pager log -1", "-P show --stat",
         "branch", "branch -a -v", "branch --show-current", "branch --list 'task/*'", "branch --contains=HEAD",
         "branch --merged", "branch --color", "branch --color=always -a", "branch --column=never", "branch --abbrev=8 -v",
         "branch --list --color x", "tag", "tag -l 'v*'", "tag -n5", "tag --column", "tag -l --column=never 'v*'",
         "worktree list --porcelain", "stash list", "stash list --oneline", "stash show", "stash show -p stash@{0}",
         "remote -v", "remote show origin", "remote get-url origin", "config user.name", "config --get user.name",
         "config -l", "config get user.name", "config list --show-origin", "reflog", "reflog HEAD", "reflog main@{0}",
         "reflog refs/heads/main", "reflog show main", "reflog show --all", "reflog list", "reflog exists main",
         "notes", "notes list", "notes show HEAD", "--version", "version --build-options"]
REFUSED_READS = ["reflog main", "reflog -n 5 main", "log -n 5", "grep -e x", "branch --contains HEAD", "branch -av",
                 "tag --column=never -l 'v*'", "remote -v show origin", "worktree list --expire now", "stash show -q",
                 "-c color.ui=never log", "--git-dir=/code/proj/.git log"]
# refused, and on real git each writes (the reviews' and QA's cases among them) ...
REAL_WRITES = ["branch x", "branch --color x", "branch --column x", "branch --abbrev x", "branch --color=always x",
               "branch -v --no-color x", "branch --sort refname x", "tag --column v2", "tag v2", "stash -m list",
               "stash --message show", "stash", "stash drop", "reflog drop main", "reflog drop --all",
               "reflog write refs/heads/main @OID@ @OID@ probe", "reflog expire --all --expire=now --expire-unreachable=now",
               "reflog delete main@{0}", "worktree add ../wt", "checkout -b z", "switch -c z", "config user.name y",
               "config set user.name y", "notes add -f -m y", "update-ref refs/heads/q HEAD", "remote add up ../origin",
               "commit --allow-empty -m q", "reset --hard HEAD~1"]
# ... or runs the program a global option or its own option names
REAL_PROGRAMS = [f"-c core.fsmonitor={PROGRAM} status", f"ls-remote --upload-pack={PROGRAM} origin",
                 f"ls-remote --upload-pack {PROGRAM} origin", f"-c diff.external={PROGRAM} diff HEAD~1",
                 f"-c alias.x=!{PROGRAM} x", "--config-env=core.fsmonitor=EDITOR status"]  # run_git: EDITOR is the program
# a git command in Herdwright's own repo that names a path outside it
NAMES_A_PATH_OUTSIDE = ["worktree add -b task/x ../wt main", "worktree remove ../wt", "remote add up /x",
                        "remote set-url origin /x", "diff --output=/code/proj/x.patch"]
# ... or names a program for git to run, or sets configuration that can name one
RUNS_A_PROGRAM = ["-c core.pager=touch log", "-c core.fsmonitor=touch status", "--config-env=core.pager=X log", "--exec-path=/tmp log",
                  "-p log", "ls-remote --upload-pack=touch origin", "ls-remote --upload-pack touch origin", "grep -O x", "grep -Ovim x",
                  "grep --open-files-in-pager x", "config edit", "config alias.z '!touch x'", "config set core.editor touch",
                  "config --add core.pager touch", "filter-branch -f --tree-filter 'touch x' HEAD", "bisect run make", "rebase -x make main",
                  "rebase --exec=make main", "submodule foreach 'touch x'", "difftool -x touch", "clone -u touch . ../c",
                  "send-email --smtp-server=/tmp/x a.patch"]

# Command lines run from Herdwright's own folder ({home}) that reach the project repo {repo}: each is refused, and run
# for real in bash and zsh each writes the repo or runs the program {mark}. (shells it runs in, command line)
BOTH, ZSH = ("bash", "zsh"), ("zsh",)
ENVIRONMENT = [  # a variable moves git to the project repo, or names a program for a read to run
    "GIT_DIR={repo}/.git git branch evil",
    "export GIT_DIR={repo}/.git; git branch evil",
    "env GIT_DIR={repo}/.git GIT_WORK_TREE={repo} git stash drop",
    "sudo() {{ env \"$@\"; }}; GIT_DIR={repo}/.git command git tag evil",
    "export GIT_EXTERNAL_DIFF={mark}; git -C {repo} diff HEAD~1 HEAD",
    "export GIT_EXTERNAL_DIFF={mark} && git -C {repo} diff HEAD~1 HEAD",
    "declare -x GIT_DIR={repo}/.git; git branch evil",
    "typeset -x GIT_DIR={repo}/.git; git branch evil",
    "G={repo}/.git; GIT_DIR=$G git branch evil",
    "read -r GIT_DIR <<< {repo}/.git; export GIT_DIR; git branch evil",
    ": ${{GIT_DIR:={repo}/.git}}; export GIT_DIR; git branch evil",
    "for GIT_DIR in {repo}/.git; do export GIT_DIR; git branch evil; done",
    "printf -v GIT_DIR %s {repo}/.git; export GIT_DIR; git branch evil",
    "N=GIT_DIR; export $N={repo}/.git; git branch evil",
    "HOME={repo}; cd; touch x.txt",
    "CDPATH={parent} cd repo && touch x.txt",
]
CONTINUATION = [  # a backslash-newline hid the next line
    "git -C {repo} log \\\n --output={repo}/out.txt",
    "git -C {repo} ls-remote \\\n --upload-pack={mark} origin",
    "touch \\\n {repo}/x.txt",
    "cp {repo}/a.py \\\n {repo}/b.txt",
    "rm -f \\\n {repo}/a.py",
    "touch \"{repo}/x\\\n.txt\"",
]
EXPANSION = [  # more of the same kind as CONTINUATION: a word only the shell knows touches a path
    "X=--output={repo}/out.txt; git -C {repo} log $X",
    "git -C {repo} log $'--output={repo}/out.txt'",
    "echo tag evil | xargs git -C {repo}",
    "echo {repo}/x.txt | xargs touch",
    "D={repo}; touch $D/x.txt",
    "D={repo}; cd $D && touch x.txt",
    "D={repo}; git -C $D tag evil",
    "D={repo}; echo x > $D/x.txt",
    "X=touch; $X {repo}/x.txt",
    "O=--output={repo}/out.txt; git log $O",
    "for d in {repo}; do touch $d/x.txt; done",
    "f() {{ touch \"$1\"; }}; f {repo}/x.txt",
    "a=({repo}/x.txt); touch \"${{a[@]}}\"",
    "rm -f {parent}/rep?/a.py",
    "mkdir {home}/d && rm -f {home}/*/../../repo/a.py",
    "touch {{{repo}/x.txt,y}}",
]
UNREAD = [  # what the old lexer misread, and code the guard was never shown
    "echo a#b; touch {repo}/x.txt",
    "touch \";\" {repo}/x.txt",
    "echo \">\" x && touch {repo}/x.txt",
    "function f {{ touch {repo}/x.txt; }}; f",
    "eval \"touch {repo}/x.txt\"",
    "sh -c 'touch {repo}/x.txt'",
    "bash <<'EOF'\ntouch {repo}/x.txt\nEOF",
    "trap 'touch {repo}/x.txt' EXIT",
    "echo $(touch {repo}/x.txt)",
    "echo \"$(cd {repo} && touch x.txt)\"",
    "echo `touch {repo}/x.txt`",
    "cat <<EOF\n$(touch {repo}/x.txt)\nEOF",
    "cat <(touch {repo}/x.txt)",
    "echo x >| {repo}/x.txt",
    "echo x <> {repo}/x.txt",
    "unlink {repo}/a.py",
    "find {home} -maxdepth 0 -exec touch {repo}/x.txt \\;",
    "find {repo} -name a.py -execdir touch x.txt \\;",
    "find {home} -maxdepth 0 -exec git -C {repo} tag evil \\;",
    "git worktree add -q {repo}/wt",
    "git diff --output={repo}/out.txt",
    "git init -q {repo}/sub",
    "git format-patch -q -1 -o {repo}/patches",
    "git --git-dir={repo}/.git --work-tree=. tag evil",
    "git push -q file://{repo} HEAD:refs/heads/evil",
    (("bash",), "PS4='$(touch {repo}/x.txt)'; set -x; true"),
    (("bash",), "x='a[$(touch {repo}/x.txt)]'; echo $((x))"),
    (("bash",), "hash -p /usr/bin/touch git; git {repo}/x.txt"),
    (("bash",), "shopt -s expand_aliases\\nalias g='touch {repo}/x.txt'\\ng"),
    (ZSH, "repeat 1 touch {repo}/x.txt"),
    (ZSH, "echo x >! {repo}/x.txt"),
    (ZSH, "ls {repo}/*(e:'touch {repo}/x.txt':)"),
    "env -C {repo} touch x.txt",
]
ALLOWED_BY_REVIEW = [  # command lines a review found allowed, each writing the project repo
    "(( x = 1 << true ))\ntouch {repo}/x.txt\ntrue",
    "[[ 1 -lt 2 ]] && (( y = 2 << 1 )) || :\ntouch {repo}/x.txt\n1",
    "echo $((touch {repo}/x.txt); (true))",
    "bash <<< 'touch {repo}/x.txt'",
    "sh -s <<< 'touch {repo}/x.txt'",
    "zsh <<< 'touch {repo}/x.txt'",
    "bash -s -- a <<< 'touch {repo}/x.txt'",
    "source /dev/stdin <<< 'touch {repo}/x.txt'",
    ". /dev/stdin <<'EOF'\ntouch {repo}/x.txt\nEOF",
    "bash /dev/stdin <<< 'touch {repo}/x.txt'",
    "echo 'touch {repo}/x.txt' | bash /dev/stdin",
    (ZSH, "echo hi >>| {repo}/x.txt"),
    (ZSH, "echo hi &>| {repo}/x.txt"),
    (ZSH, "echo hi >&| {repo}/x.txt"),
    (ZSH, "echo hi &>>| {repo}/x.txt"),
    (ZSH, "cd home repo && touch x.txt"),
    (ZSH, "cd home repo && git tag evil"),
    (ZSH, "cd home repo; git commit -q --allow-empty -m evil"),
    (ZSH, "if [[ 1 == 1 ]] touch {repo}/x.txt"),
    (ZSH, "while [[ ! -e {repo}/x.txt ]] touch {repo}/x.txt"),
    (ZSH, "=touch {repo}/x.txt"),
    (ZSH, "emulate sh -c 'touch {repo}/x.txt'"),
    "git -c alias.x='!touch {repo}/x.txt' x",
    "git -c core.editor='touch {repo}/x.txt' commit --allow-empty",
    "git -c core.sshCommand='touch {repo}/x.txt' ls-remote ssh://x/y",
    "git -c core.fsmonitor={mark} status",
    "git config alias.z '!touch {repo}/x.txt'; git z",
    "git filter-branch -f --tree-filter 'touch {repo}/x.txt' HEAD",
    "mktemp --tmpdir={repo}",
    ("csh", "csh -c 'touch {repo}/x.txt'"),
    ("tcsh", "tcsh -c 'touch {repo}/x.txt'"),
    ("arch", "arch -arch arm64 touch {repo}/x.txt"),
    (ZSH, "path+={parent}; mark"),
]
ALLOWED_BY_QA = [  # ... and the ones a QA found
    "cat <<EOF\nEO\\\nF\ntouch {repo}/x.txt\nEOF",
    "cat <<EOF\nE\\\nOF\ngit -C {repo} tag evil\nEOF",
    "bash <<< \"touch {repo}/x.txt\"",
    "sh <<< 'git -C {repo} tag evil'",
    (ZSH, "cd -q {repo}; touch x.txt"),
    (ZSH, "cd -s {repo}; touch x.txt"),
    (ZSH, "cd -qP {repo}; touch x.txt"),
    (ZSH, "chdir {repo}; git tag evil"),
    (ZSH, "pushd -q {repo} && rm a.py"),
    (ZSH, "true | cd {repo}; touch x.txt"),
    (ZSH, "echo hi | cd {repo} && rm a.py"),
    (ZSH, "set -a; : ${{GIT_DIR::={repo}/.git}}; git branch evil"),
    (ZSH, "set -a; print -v GIT_DIR {repo}/.git; git tag evil"),
    (ZSH, "setopt allexport; print -v GIT_DIR {repo}/.git; git tag evil"),
    (ZSH, "set -a; zstyle :x y {repo}/.git; zstyle -s :x y GIT_DIR; git branch evil"),
    (ZSH, "=rm {repo}/a.py"),
    (ZSH, "if [[ -n 1 ]] git -C {repo} tag evil"),
    (ZSH, "emulate zsh -c 'rm {repo}/a.py'"),
    (ZSH, "functions[f]='touch {repo}/x.txt'; f"),
    (ZSH, "hash t=/usr/bin/touch; t {repo}/x.txt"),
    (ZSH, "setopt extendedglob; rm -f {parent}/^home/a.py"),
    (ZSH, "zcompile {repo}/a.py"),
    (ZSH, "zmodload zsh/files; zf_rm {repo}/a.py"),
    (("bash",), "history -w {repo}/x.txt"),
    ("xcrun", "xcrun touch {repo}/x.txt"),
    ("script", "script -q /dev/null touch {repo}/x.txt"),
    "find {parent} -name a.py -delete",
    "find {parent} -name a.py -exec rm {{}} \\;",
    "git init -q {home}/g; git -C {home}/g config alias.t '!touch {repo}/x.txt'; git -C {home}/g t",
]
FAILED_CD = [  # a `cd` that fails was followed, so the next command was checked in a folder never entered
    "cd {home}/no/pe; touch ../repo/x.txt",
    "cd {home}/no/pe\ntouch ../repo/x.txt",
    "cd {home}/afile/x; touch ../repo/x.txt",
    "cd {home}/afile/x; rm ../repo/a.py",
    "cd {home}/no/pe; echo hi > ../repo/x.txt",
    "cd {home}/no/pe; cp afile ../repo/x.txt",
    "cd {home}/no/pe; dd if=afile of=../repo/x.txt",
    "cd {home}/no/pe; find ../repo -name a.py -delete",
    "cd {home}/no/pe; git -C ../repo tag evil",
    "cd {home}/no/pe; git -C ../repo commit -q --allow-empty -m evil",
    "cd {home}/no/pe || touch ../repo/x.txt",
    "cd {home}/no/pe || true; touch ../repo/x.txt",
    "cd {home}/no/pe && true; touch ../repo/x.txt",
    # ... and a `cd` to a path the line itself changes first: the folder is there when the line is checked
    "rm -rf {home}/sub; cd {home}/sub/x; touch ../repo/x.txt",
    "mv {home}/sub {home}/sub2; cd {home}/sub/x; touch ../repo/x.txt",
    "ln -sfn {home} {home}/link; cd {home}/link; touch ../repo/x.txt",
]
LINKS = [  # a link's relative target was read from where the shell stands, and a path through a new link as if no link were there
    "mkdir {home}/d; ln -s ../../repo {home}/d/l; touch {home}/d/l/x.txt",
    "mkdir {home}/d && ln -s ../../repo {home}/d/l && echo hi > {home}/d/l/x.txt",
    "mkdir {home}/d; ln -s ../../repo {home}/d/l; cd {home}/d/l; touch x.txt",
    "ln -s {home}/../home {home}/h; cd {home}/h; touch ../repo/x.txt",
    "ln -s {home} {home}/sub/k; touch {home}/sub/k/../repo/x.txt",
    "ln -s {home} {home}/sub/k; echo hi > {home}/sub/k/../repo/x.txt",
    "ln -s {home} {home}/sub/k; git -C {home}/sub/k/../repo tag evil",
    "mv {home}/link {home}/m; touch {home}/m/../../repo/x.txt",
]
UNLISTED_WORDS = [  # a word or a program nobody listed before the command, and a sed script that writes
    (ZSH, "true; - touch {repo}/x.txt"),
    (ZSH, "true; - git -C {repo} tag evil"),
    (ZSH, "true; - sh -c 'touch {repo}/x.txt'"),
    (ZSH, "typeset -fu zargs; zargs -- {repo}/x.txt -- touch"),
    (ZSH, "typeset -fu zmv; zmv {repo}/a.py {repo}/b.py"),
    (("bash",), "compgen -C 'touch {repo}/x.txt'"),
    "sed -n 'w {repo}/x.txt' {repo}/a.py",
    "sed 's/x/y/w {repo}/x.txt' {repo}/a.py",
    "sed -n -e p -e 'w {repo}/x.txt' {repo}/a.py",
    ("taskpolicy", "taskpolicy -b touch {repo}/x.txt"),
    ("taskpolicy", "taskpolicy -c utility touch {repo}/x.txt"),
    ("lockf", "lockf {home}/l touch {repo}/x.txt"),
    ("lockf", "lockf -k {repo}/x.lock true"),
    ("flock", "flock {home}/l touch {repo}/x.txt"),
    ("chronic", "chronic touch {repo}/x.txt"),
    ("apply", "apply touch {repo}/x.txt"),
    ("sandbox-exec", "sandbox-exec -p '(version 1)(allow default)' touch {repo}/x.txt"),
]
# (shells, line); a case that names a program instead of shells runs in both where the machine has that program
REACHES_A_PROJECT = [c if isinstance(c, tuple) and not isinstance(c[0], str) else (BOTH, c) if isinstance(c, str) else
                     ((BOTH if shutil.which(c[0]) else ()), c[1])
                     for c in ENVIRONMENT + CONTINUATION + EXPANSION + UNREAD + ALLOWED_BY_REVIEW + ALLOWED_BY_QA + FAILED_CD + LINKS + UNLISTED_WORDS]
# ... and command lines that keep to the grammar and stay allowed: run for real, they leave the project repo as it was
STAYS_ALLOWED = [
    "git -C {repo} log --oneline \\\n -3",
    "echo 'GIT_DIR=x is not set here' \"a;b\" > {home}/n.txt",
    "git commit -q --allow-empty -m \"HW | a message (with) 'quotes'\n\na second line; no \\$(command) and no \\`command\\` is run\"",
    "ls {repo}/*.py && grep -rn x {repo} | head -3",
    "echo \"$HOME\" ${{USER}} 2>&1 | cat > {home}/out.txt",
    "touch {home}/a.tmp {home}/b.tmp && rm -f {home}/a.tmp",
    "git -C {repo} log --format='%h $s' -- '*.py'",
    "git -C {repo} show --stat HEAD~1 && git -C {repo} diff HEAD~1 HEAD",
    "LC_ALL=C git log --oneline -1 && FOO=1 env BAR=2 ls",
    "cd {home} && touch n.txt && find {repo} -name '*.py' -exec grep -l x {{}} +",
    "cat < {repo}/a.py >> {home}/copy.txt; test -f {repo}/a.py || echo gone",
    "cd {home}/sub && touch ../n.txt && echo hi > ../o.txt",
    "mkdir -p {home}/new/x && touch {home}/new/x/n.txt; rm -f {home}/afile; echo hi > {home}/afile",
    "sed -n '1,2p' {repo}/a.py | sed -e 's/wip/done/' -e '/warn/!d' > {home}/o.txt",
    "sed -n p {repo}/a.py > {home}/o.txt && ln -s afile {home}/l2 && nice -n 5 env -i ls {repo}",
]


class PretoolTests(unittest.TestCase):
    def setUp(self):
        # the tree of these tests (/hw-home, /code/proj) is not on disk: every folder a `cd` names counts as there.
        # The tests that run a real shell put the real check back (CAN_ENTER).
        on_disk = unittest.mock.patch.object(hwshell, "can_enter", lambda path: True)
        on_disk.start()
        self.addCleanup(on_disk.stop)

    def test_only_the_lock_holder_is_constrained(self):
        self.assertIsNone(decide("Write", sid="worker", holder=COORD, file_path="/code/proj/a.py"))
        self.assertIsNone(decide("Write", holder=None, file_path="/code/proj/a.py"))
        self.assertIsNotNone(decide("Write", file_path="/code/proj/a.py"))

    def test_edits(self):
        self.assertIsNone(decide("Edit", file_path="/hw-home/bin/hw"))
        self.assertIsNone(decide("Write", file_path="/hw-home/profile/briefs/T1.md"))
        self.assertIsNone(decide("Write", file_path="/tmp/scratch.txt"))
        self.assertIsNone(decide("Write", file_path="notes.md", cwd="/hw-home"))
        self.assertIn("spawn a worker", decide("Edit", file_path="/code/proj/a.py"))
        self.assertIsNotNone(decide("NotebookEdit", notebook_path="/code/proj/n.ipynb"))
        self.assertIsNotNone(decide("Edit", file_path="/hw-home/../code/x.py"))

    def test_reads_are_never_blocked(self):
        for tool in ("Read", "Grep", "Glob", "WebFetch"):
            self.assertIsNone(decide(tool, file_path="/code/proj/a.py", pattern="x"))
        for cmd in ("ls /code/proj", "git -C /code/proj log --oneline", "git -C /code/proj status", "cat /code/proj/a.py | head",
                    "grep -rn foo /code/proj", "gh pr view 1", "bin/hw status"):
            self.assertIsNone(bash(cmd), cmd)

    def test_git_writes(self):
        self.assertIsNone(bash("git commit -m x"))                       # cwd is Herdwright's repo
        self.assertIsNone(bash("git add -A && git commit -qm 'HW | x'"))
        self.assertIsNone(bash("git -C /hw-home commit -m x", cwd="/code/proj"))
        self.assertIn("git commit", bash("git commit -m x", cwd="/code/proj"))
        self.assertIn("git push", bash("git -C /code/proj push origin main"))
        self.assertIsNotNone(bash("git -c user.name=x -C /code/proj reset --hard"))
        self.assertIsNotNone(bash("echo hi && git rebase main", cwd="/code/proj"))

    def test_only_git_reads_run_in_a_project_repo(self):
        writes = ["worktree add -b task/x ../wt main", "worktree remove ../wt", "worktree prune", "branch task/x",
                  "branch -D task/x", "branch -m a b", "branch --set-upstream-to=origin/main", "checkout -b x",
                  "switch -c x", "fetch origin main", "pull", "init", "update-ref refs/heads/x HEAD", "gc", "notes add -m x",
                  "tag v1", "tag -d v1", "stash", "stash pop", "remote add up /x", "remote set-url origin /x",
                  "config user.name x", "config --unset user.name", "config set user.name x", "config edit",
                  "reflog expire --all", "submodule update", "diff --output=/code/proj/x.patch", "grep -Ovim x",
                  "commit -m x", "some-alias", "branch --color x", "branch --column x", "branch --abbrev x",
                  "branch --color=always x", "branch -v --no-color x", "tag --column v1", "tag --color v1",
                  "reflog delete HEAD@{1}", "reflog drop main", "reflog drop --all", "reflog write refs/heads/main 0000 1111 x",
                  "reflog -n1 drop main", "reflog drop", "stash -m list", "stash --message show", "stash -q list",
                  # an option that makes a read run a program from git's configuration
                  "diff --ext-diff", "log -p --textconv", "show --textconv HEAD:a", "cat-file --textconv HEAD:a", "cat-file --filters HEAD:a"]
        for sub in writes + RUNS_A_PROGRAM:
            with self.subTest(sub):
                self.assertIn("Project repos are changed only by workers", bash(f"git -C /code/proj {sub}"))
                self.assertIsNotNone(bash(f"git {sub}", cwd="/code/proj"))
                if sub in NAMES_A_PATH_OUTSIDE:  # from Herdwright's own repo it would still write out there
                    self.assertIn("outside Herdwright's repo/profile/tmp", bash(f"git {sub}"))
                elif sub in RUNS_A_PROGRAM:  # `git -c alias.x='!touch <project>/x' x` ran from Herdwright's own repo
                    self.assertIn("does not follow a program git is told to run", bash(f"git {sub}"))
                else:
                    self.assertIsNone(bash(f"git {sub}"))  # Herdwright's own repo
        for sub in ("config user.name x", "config --unset user.email", "config set user.name x", "config --get alias.z", "clean -x -f",
                    "cherry-pick -x HEAD", "rebase main", "bisect start", "submodule update"):
            self.assertIsNone(bash(f"git {sub}"), sub)
        self.assertIsNotNone(bash("git --git-dir=/code/proj/.git worktree add ../wt"))  # placed by --git-dir too
        for sub in READS:
            with self.subTest(sub):
                self.assertIsNone(bash(f"git -C /code/proj {sub}"))
        for sub in REFUSED_READS:  # harmless, but outside the strict forms: the coordinator writes them another way
            with self.subTest(sub):
                self.assertIsNotNone(bash(f"git -C /code/proj {sub}"))

    def test_a_refused_read_is_told_what_to_write_instead(self):
        """`git log -n 5` was answered "only commands that read run (status, log, ...)", which names `log`
        as allowed and gives nothing to retry with."""
        for sub, hint in (("log -n 5", "`-n` is not an option the guard takes here"), ("log -n 5", "value attached (`-n5`"),
                          ("shortlog -sn", "short options one by one"), ("branch -v --list x", "listing word right after the subcommand"),
                          ("reflog main", "`reflog show <ref>`"), ("stash -q list", "reads only as `git stash list|show`"),
                          ("-c color.ui=never log", "`-c` may not stand before the subcommand"),
                          ("--git-dir=/code/proj/.git log", "only `-C <dir>`, `--no-pager`"),
                          ("branch x", "takes no name, so `x` makes it a write"), ("fetch", "is not a command that only reads")):
            with self.subTest(sub):
                self.assertIn(hint, bash(f"git -C /code/proj {sub}"))
                self.assertIn("goes to one too", bash(f"git -C /code/proj {sub}"))  # ... or hand it to a worker
        self.assertIn("a variable is set before `git`", bash("LC_ALL=C git -C /code/proj log -1"))
        self.assertIn("quote a pattern meant for git", bash("git -C /code/proj log -- *.py"))
        self.assertIsNone(bash("git -C /code/proj log -- '*.py'"))

    def test_no_command_line_sets_a_variable_git_or_a_program_would_follow(self):
        """`GIT_DIR=<project>/.git git branch evil` from Herdwright's own folder wrote the project repo, as did
        the `export` and `env` forms; so did zsh's own ways to set a variable (`print -v`, `zstyle -s`) once
        `set -a` exports it. The rule: no command line names a GIT_* variable, or one that names a program or moves a
        path lookup, where the shell could set it: before the program, or after any program not known to set nothing."""
        for line in ("GIT_DIR=/code/proj/.git git branch evil", "export GIT_DIR=/code/proj/.git; git branch evil",
                     "env GIT_DIR=/code/proj/.git GIT_WORK_TREE=/code/proj git stash", "export GIT_DIR=/code/proj/.git && bin/hw status",
                     "declare -x GIT_DIR=/x", "typeset -gx GIT_WORK_TREE=/x", "readonly GIT_DIR=/x", "export GIT_DIR", "export GIT_DIR+=/x",
                     "read -r GIT_DIR", "printf -v GIT_DIR %s /x", "printf -vGIT_DIR %s /x", "G=/x; GIT_DIR=$G git log", "export 'GIT_'DIR=/x",
                     "sudo GIT_DIR=/x git log", "xargs env GIT_DIR=/x git log", "find . -exec env GIT_DIR=/x git tag x \\;",
                     "GIT_PAGER=cat git log", "GIT_EXTERNAL_DIFF=touch git diff", "GIT_SSH_COMMAND=x git -C /code/proj ls-remote origin",
                     "export GIT_EXTERNAL_DIFF=touch; git -C /code/proj diff HEAD~1", "GIT_EDITOR=true git commit --amend",
                     "declare -n r=GIT_DIR", "unset HOME", "some-builtin -x GIT_DIR /x", "env -u PATH ls",
                     # the same for a variable that names a program a read runs, or moves a lookup the guard relies on
                     "PAGER=touch git -C /code/proj log", "export PAGER=touch", "EDITOR=x git commit", "VISUAL=x ls", "LESSOPEN='|x %s' git log",
                     "MANPAGER=x git help log", "SSH_ASKPASS=x git ls-remote origin", "PATH=/x:$PATH git log", "export PATH=/x",
                     "HOME=/code/proj; cd /tmp; touch x", "XDG_CONFIG_HOME=/x git log", "LD_PRELOAD=/x ls", "TMPDIR=/code/proj mktemp",
                     "DYLD_INSERT_LIBRARIES=/x git log", "BASH_ENV=/x bash run.sh", "ZDOTDIR=/x zsh run.sh", "IFS=/ ls", "NULLCMD=touch",
                     "path+=/code/proj; mark", "typeset fpath=/x"):  # zsh ties `path` to PATH
            with self.subTest(line):
                self.assertIn("this command line sets `", bash(line))
        self.assertIn("sets `GIT_DIR`", bash("GIT_DIR=/code/proj/.git git branch evil"))
        self.assertIn("`git -C <dir>`, `--no-pager`", bash("GIT_PAGER=cat git log"))  # what to write instead
        # the ways to set one that the grammar refuses outright
        for line in ("read -r GIT_DIR <<< /x", "for GIT_DIR in /x; do git status; done", ": ${GIT_DIR:=/x}", ": ${GIT_DIR::=/x}",
                     "export $N=/x", "declare -x \"$N\"", "read $N", "if true; then GIT_INDEX_FILE=/x git add a; fi",
                     "(GIT_CONFIG_GLOBAL=/x git log)", "echo $(GIT_DIR=/x git log)", "CDPATH=/code cd proj", "set -a; GIT_DIR=/x",
                     "print -v GIT_DIR /x", "setopt allexport", "zstyle -s :x y GIT_DIR", "PS4='$(ls)' bash -x run.sh"):
            with self.subTest(line):
                self.assertIsNotNone(bash(line))
        for line in ("FOO=1 ls", "LC_ALL=C git log", "export FOO=bar", "TZ=UTC date", "printf '%s' \"$x\"", "read x", "echo $PATH \"${HOME}/x\"",
                     "git log --grep=GIT_DIR", "echo GIT_DIR=x 'PATH=y'", "git commit -m HOME=x", "grep -rn GIT_DIR bin",
                     "bin/hw log T1 'the PATH question' kind=decision", "ls path=x /tmp/path"):
            with self.subTest(line):
                self.assertIsNone(bash(line))

    def test_a_backslash_newline_joins_two_lines_as_the_shell_does(self):
        """The guard read a continued line as a new command, so every word on it was hidden from it."""
        for line in ("git -C /code/proj log \\\n --output=/code/proj/out.txt", "git -C /code/proj ls-remote \\\n --upload-pack=touch origin",
                     "touch \\\n /code/proj/x.txt", "cp a.txt \\\n /code/proj/b.txt", "rm -f \\\n /code/proj/a.py",
                     "git -C /code/proj \\\n tag evil", "echo x \\\n > /code/proj/x.txt", "touch /code/pr\\\noj/x.txt",
                     "touch \"/code/proj/x\\\n.txt\"", "cd /code/proj \\\n && ls"):
            with self.subTest(line):
                self.assertIsNotNone(bash(line))
        self.assertEqual(hwshell.tokens("touch \\\n a 'b\\\nc' \"d\\\ne\""), ["touch", "a", "b\\\nc", "de"])  # as bash and zsh
        for line in ("git -C /code/proj log --oneline \\\n -3", "touch \\\n notes.md", "echo a \\\n b"):
            self.assertIsNone(bash(line), line)

    def test_what_the_guard_cannot_read_with_certainty_is_refused(self):
        """The guard reads a small grammar (hwshell.GRAMMAR) and refuses every construct outside it, whatever it would
        do; the refusal names the construct and says what the guard reads."""
        for construct, piece in REFUSED:
            line = construct.format(w="true")
            with self.subTest(line):
                self.assertIn("reads only plain command lines and refuses the rest. Refused here: ", bash(line))
                self.assertIn(piece, bash(line))
                self.assertIn("What it reads: plain words; text in '...'", bash(line))
        for line, how in (("git commit -m \"$(cat <<'EOF'\nHW | x\nEOF\n)\"", "run that command first and write its output out"),
                          ("cat > /tmp/a.md <<'EOF'\nx\nEOF", "write the text in quotes, or put it in a file"),
                          ("ls # the briefs", "leave it out"), ("for f in a b; do echo $f; done", "Write one plain command after the other"),
                          ("sh -c 'ls'", "write the commands themselves"), ("touch $D/x", "Quote a pattern meant for"),
                          ("cd - && ls", "`cd <path written out>`")):  # ... and how to write it instead
            self.assertIn(how, bash(line), line)
        # a word the shell expands where the guard must know it
        for line, what in (("$X a", "cannot tell which program runs"), ("touch $D/x", "the shell expands `$D/x`"),
                           ("ls | xargs rm", "the shell expands `<what xargs reads>`"), ("git $X log", "cannot tell which repo"),
                           ("git -C $D status", "cannot tell which repo"), ("git log $X", "cannot tell what git is given"),
                           ("git add \"$F\"", "cannot tell what git is given"), ("ls | xargs git add", "cannot tell what git is given"),
                           ("git add profile/*.md", "names a path by a pattern"), ("git -C /code/proj log ^main", "cannot tell what git is given")):
            with self.subTest(line):
                self.assertIn(what, bash(line))
        # what keeps to the grammar runs: the coordinator's everyday lines
        for line in ("git commit -m \"HW | x (y) 'z'\n\nsecond line; \\`w\\` \\$(v)\"", "git commit -qam \"T$N done\"", "git tag -m \"$M\" v1",
                     "bin/hw relay Q1 \"the user's answer, verbatim: (a) — yes; go\"", "echo \"$HOME\" ${USER} $?", "cd /tmp && ls",
                     "echo '$(not run)' '`nor this`'", "bash run.sh", "ls | xargs grep x", "git add profile/briefs profile/STATUS.md",
                     "rm -f profile/.cache/a.tmp", "git diff HEAD@{1} stash@{0}", "git log HEAD~3..HEAD^", "find . -name '*.py' -exec grep -l x {} +",
                     "echo a#b ';' \">\" x", "git push origin HEAD", "git remote add origin git@github.com:me/herdwright.git",
                     "git fetch https://github.com/a/b main", "echo '$(not run)' > notes.md", "git commit -m 'uses $(x) and `y`'",
                     "ls profile/briefs/*.md | head -3", "cat profile/briefs/T1*.md 2>&1 | wc -l", "test -f /tmp/x || echo none",
                     "gh pr view 1 --json state", "herdr pane list", "sleep 5; bin/hw status"):
            with self.subTest(line):
                self.assertIsNone(bash(line))
        # ... and a write it reads is refused where it reaches a project, as before
        for line in ("touch \";\" /code/proj/x", "echo a#b; touch /code/proj/x", "noglob touch /code/proj/x", "unlink /code/proj/x",
                     "sed --in-place s/a/b/ /code/proj/x", "find . -fprint /code/proj/x", "find . -exec cp {} /code/proj/ \\;",
                     "find . -exec git -C /code/proj tag x \\;", "find /code/proj/a -execdir touch x \\;", "git worktree add /code/proj/wt",
                     "git init /code/proj/sub", "git format-patch -1 -o /code/proj/p", "git archive -o/code/proj/a.tar HEAD",
                     "git bundle create ../../code/proj/b HEAD", "git clone . ~/.ssh/x", "git --git-dir=/code/proj/.git --work-tree=/hw-home add .",
                     "git --work-tree /code/proj checkout -- .", "git push file:///code/proj HEAD:refs/heads/x", "git --exec-path=/code/proj/bin status",
                     "arch -arm64 touch /code/proj/x", "arch -arch arm64 touch /code/proj/x", "xcrun touch /code/proj/x", "env -P /usr/bin touch /code/proj/x",
                     "mktemp --tmpdir=/code/proj", "cp --target-directory=/code/proj a", "cp -t/code/proj a", "watch -d touch /code/proj/x", "read 'a[$(touch /code/proj/x)]'", "x='a[$(touch /code/proj/x)]'",
                     "test -v 'a[$(touch /code/proj/x)]'", "ls | xargs -I{} touch {}/x", "ls | xargs -I{} find {} -delete"):
            with self.subTest(line):
                self.assertIsNotNone(bash(line))

    def test_no_line_built_from_a_refused_construct_is_allowed(self):
        """A fuzz-style probe: every refused construct, with a harmless command or a write in it, in each place of a
        command line, and at a random place among plain commands (tests/grammar_cases.py)."""
        lines = list(refused_lines())
        for line in lines:
            if not bash(line):
                self.fail(f"allowed: {line!r}")
        self.assertGreater(len(lines), 1500)

    def test_the_commands_the_coordinators_own_documents_name_keep_to_the_grammar(self):
        """AGENTS.md, the skills and the README tell the coordinator which commands to run: none may be refused. (Not
        the README's install lines, which the user runs, and not a mention of several scripts at once: `bin/hw-*`.)"""
        documents = [_load.HOME / "AGENTS.md", _load.HOME / "README.md", *sorted((_load.HOME / ".claude" / "skills").glob("*/SKILL.md"))]
        commands = set()
        for document in documents:
            for span in re.findall(r"`((?:bin/hw|git |herdr |gh |claude |\(cd )[^`]*)`", document.read_text()):
                span = re.sub(r"\[[^\]]*\]", "", " ".join(span.split()))  # an optional part: `[hat=build|review|qa]`
                if not span.startswith("git clone") and "*" not in span:
                    commands.add(re.sub(r"<[^>]*>", "x", span).replace("…", "x").replace("...", "x"))
        self.assertGreater(len(commands), 40)
        for command in sorted(commands):
            with self.subTest(command):
                self.assertIsNone(bash(command))

    def test_a_guard_that_trips_over_a_command_refuses_it(self):
        with unittest.mock.patch.object(pt, "commands_problem", side_effect=IndexError("boom")):
            self.assertIn("could not check this command line (IndexError)", bash("ls"))

    @unittest.skipUnless(all(map(shutil.which, ("git", "bash", "zsh"))), "needs git, bash and zsh")
    def test_on_a_real_shell_every_refused_route_reaches_the_project_repo(self):
        """Each refused command line, run for real in bash and in zsh from
        Herdwright's own folder, writes the project repo or runs the program, so each case proves something; each
        allowed one of the same shapes leaves the repo as it was."""
        with tempfile.TemporaryDirectory() as root, unittest.mock.patch.object(hwshell, "can_enter", CAN_ENTER):
            scratch = Scratch(root)
            guard = lambda line: pt.bash_problem(line, str(scratch.home), [scratch.home], [scratch.repo])
            for shells, case in REACHES_A_PROJECT:
                line = scratch.line(case)
                for shell in shells:
                    with self.subTest(line, shell=shell):
                        self.assertIsNotNone(guard(line))
                        self.assertTrue(scratch.reaches(line, shell),
                                        "the real run neither wrote the repo nor ran the program: the case proves nothing")
            for case in STAYS_ALLOWED:
                line = scratch.line(case)
                for shell in BOTH:
                    with self.subTest(line, shell=shell):
                        self.assertIsNone(guard(line))
                        self.assertFalse(scratch.reaches(line, shell), "an allowed command line changed the project repo")

    @unittest.skipUnless(all(map(shutil.which, ("git", "bash", "zsh"))), "needs git, bash and zsh")
    def test_on_a_real_shell_a_value_attached_to_a_short_option_runs_in_a_repo_of_herdwright_s_own(self):
        """`git commit -m"fix a/b"`, `checkout -bfeat/x` and `format-patch -qo<own folder>/patches` in a repo
        the coordinator may change were refused as naming `/b`, `/x`, `/patches`, paths outside its folders."""
        with tempfile.TemporaryDirectory() as root:
            scratch = Scratch(root)
            for case in ATTACHED_VALUES:
                line = scratch.line(case)
                self.assertIsNone(pt.bash_problem(line, str(scratch.home), [scratch.home, scratch.top / "clone"], [scratch.repo]), line)
                for shell in BOTH:
                    with self.subTest(line, shell=shell):
                        self.assertFalse(scratch.reaches(line, shell), "an allowed command line changed the project repo")

    @unittest.skipUnless(all(map(shutil.which, ("git", "bash", "zsh"))), "needs git, bash and zsh")
    def test_on_a_real_shell_what_the_guard_allows_of_the_grammar_leaves_the_project_repo_as_it_was(self):
        """The second table: every form the grammar reads (grammar_cases.FORMS: a `cd` that fails or leads
        through a path the line changes, a word or a program before the command, a pipe, a redirect) around every
        command that writes the project repo. Whatever the guard allows of it is run in bash and in zsh from
        Herdwright's own folder and must leave the repo as it was. Before, `cd <not there>; touch ../repo/x.txt`,
        `- touch <repo>/x.txt` and `taskpolicy -b touch <repo>/x.txt` were allowed and wrote."""
        with tempfile.TemporaryDirectory() as root, unittest.mock.patch.object(hwshell, "can_enter", CAN_ENTER):
            scratch = Scratch(root)
            lines = [scratch.line(case) for case in form_lines(PROJECT_WRITES)]
            allowed = [line for line in lines if pt.bash_problem(line, str(scratch.home), [scratch.home], [scratch.repo]) is None]
            for line in allowed:
                for shell in BOTH:
                    with self.subTest(line, shell=shell):
                        self.assertFalse(scratch.reaches(line, shell), "an allowed command line changed the project repo")
            self.assertGreater(len(lines), 1000)
            self.assertGreater(len(allowed), 10)  # `cd <home>/sub && touch ../repo/x.txt` writes below Herdwright's own folder

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_on_real_git_every_read_leaves_the_repo_as_it_was_and_every_refused_case_writes(self):
        """An option that takes a value swallowed the next word, so a write passed as a read
        (`branch --color x`, `reflog drop`, `stash -m list`), and a read ran a program a global option or its own option
        named. Each case runs on real git: the reads change nothing, and each refused case does write or run the
        program, so the case proves something. tests/probe_git_reads.py tries every option `git <cmd> -h` names."""
        with tempfile.TemporaryDirectory() as root:
            repo = probe_repo(tempfile.mkdtemp(dir=root))
            for sub in READS:
                words = shlex.split(sub)
                with self.subTest(sub):
                    self.assertTrue(hwshell.git_only_reads(["git", *words]))
                    before = repo_state(repo)
                    run_git(repo, words)
                    self.assertFalse((repo.parent / "ran").exists(), "git ran a program")
                    self.assertEqual(repo_state(repo), before, "a read changed the repo")
            for sub in REAL_WRITES + REAL_PROGRAMS:
                repo = probe_repo(tempfile.mkdtemp(dir=root))
                oid = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
                words = shlex.split(sub.replace("@OID@", oid))
                (repo / "a.py").write_text("x = 3\n")  # a change for `stash` to take
                with self.subTest(sub):
                    self.assertFalse(hwshell.git_only_reads(["git", *words]))
                    before = repo_state(repo)
                    run_git(repo, words)
                    ran = (repo.parent / "ran").exists()
                    self.assertTrue(ran if sub in REAL_PROGRAMS else repo_state(repo) != before,
                                    "git neither wrote nor ran the program: the case proves nothing")

    def test_a_project_repo_under_a_temp_dir_is_still_a_project_repo(self):
        repo = "/tmp/scratch/demo-repo"
        self.assertIsNotNone(bash(f"git -C {repo} worktree add -b x ../x main"))
        self.assertIsNotNone(bash("git branch x", cwd=repo))
        self.assertIsNotNone(bash(f"echo x > {repo}/a.txt"))
        self.assertIsNotNone(bash(f"rm -rf /tmp/scratch/worktrees/demo-repo/x"))
        self.assertIsNotNone(decide("Write", file_path=f"{repo}/a.txt"))
        self.assertIn("don't `cd`", bash(f"cd {repo} && ls"))
        self.assertIsNone(bash(f"git -C {repo} log --oneline -3"))  # reads still run
        self.assertIsNone(bash("echo x > /tmp/scratch/notes.txt"))  # the rest of the temp dir is still scratch
        # a command on the folder above reaches into the repo without naming it
        for line in ("rm -rf /tmp/scratch", "find /tmp/scratch -name a.py -delete", "find /tmp -name '*.py' -exec rm {} +",
                     "chmod -R 000 /tmp/scratch", "mv /tmp/scratch /tmp/elsewhere", "cd /tmp && rm -rf scratch"):
            self.assertIn("that folder holds a project repo", bash(line), line)
        self.assertIsNone(bash("find /tmp/scratch -name a.py"))
        self.assertIsNone(bash("git -C /tmp/scratch/other worktree add ../y"))

    def test_project_roots_come_from_projects_json_and_never_cover_herdwright_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(os.path.realpath(tmp))
            config = root / "projects.json"
            config.write_text(json.dumps({"worktree_root": str(root / "wt"), "repos": {
                "app": {"path": str(root / "app")}, "herdwright": {"path": str(root / "hw")}, "docs": {"path": str(root)}}}))
            with unittest.mock.patch.object(pt.hwlib, "PROJECTS_PATH", config):
                self.assertEqual(pt.project_roots(hw_home=root / "hw", profile=root / "hw" / "profile"), [root / "app", root / "wt"])

    def test_cd(self):
        self.assertIsNone(bash("cd /hw-home/bin && ls"))
        self.assertIsNone(bash("cd /tmp && ls"))
        self.assertIn("don't `cd`", bash("cd /code/proj && git status"))
        self.assertIn("don't `cd`", bash("cd /tmp && cd ../code/proj"))
        self.assertIn("don't `cd`", bash("ls; cd /code/proj"))  # where it aims, followed or not
        for line in ("cd -", "cd", "cd -P /tmp", "cd /tmp /code/proj", "(cd /code/proj && git status)", "ls | cd /code/proj"):
            self.assertIn("it is followed only as `cd <path written out>`" if "(" not in line else "outside quotes", bash(line), line)

    def test_file_writers(self):
        self.assertIsNone(bash("echo x > /tmp/a"))
        self.assertIsNone(bash("echo x >> profile/notes.md"))
        self.assertIsNone(bash("echo x 2>&1 | cat"))
        self.assertIsNotNone(bash("echo x > /code/proj/a.txt"))
        self.assertIsNotNone(bash("rm -rf /code/proj/node_modules"))
        self.assertIsNotNone(bash("sed -i '' s/a/b/ /code/proj/a.py"))
        self.assertIsNone(bash("rm -f /tmp/x"))
        self.assertIsNotNone(bash("cp ~/notes.txt ~/.ssh/x"))

    def test_a_relative_write_is_resolved_where_the_shell_stands(self):
        """`touch c.txt` with OpenCode's `workdir` in a project repo passed; so did `touch ../../x` on Claude Code."""
        for command in ("touch c.txt", "rm keep.txt", "rm -rf src", "sed -i '' s/a/b/ c.txt", "mkdir -p a/b", "cp c.txt d.txt",
                        "mv c.txt d.txt", "echo x > c.txt", "echo x | tee c.txt", "perl -pi -e s/a/b/ c.txt", "ln -s a b"):
            for where in ({"workdir": "/code/proj"}, {"workdir": "../code/proj"}, {"cwd": "/code/proj"}):  # OpenCode, OpenCode, Claude Code
                self.assertIn("outside Herdwright's repo/profile/tmp", decide("shell", command=command, **where), (command, where))
            self.assertIsNone(decide("shell", command=command, workdir="profile/briefs"), command)  # in Herdwright's own repo
            self.assertIsNone(bash(command), command)
        self.assertIn("`touch` on c.txt in /code/proj", decide("shell", command="touch c.txt", workdir="/code/proj"))
        for command in ("touch ../../outside.txt", "rm ../code/proj/a.py", "echo x >> ../x.txt", "touch bin/../../x"):
            self.assertIsNotNone(bash(command), command)
        self.assertIsNone(bash("touch ../hw-home/notes.md"))
        self.assertIsNone(decide("shell", command="ls && cat c.txt", workdir="/code/proj"))  # reads stay free

    def test_a_cd_is_followed_only_as_cd_and_and_at_the_head_of_the_line_into_a_folder_that_is_there(self):
        """A `cd` may fail, and the command after `;`, `||` or a new line then runs where the shell stood.
        The guard asked whether the folder was there of the resolved path, which drops `nope/..`, and
        went back from a link as the kernel does, not as the shell does. Now only `cd <path> &&` at the head of the
        line is followed, the path asked about as written and without a `..`; after any other `cd` the directory is
        unknown, which refuses a relative write and every git command."""
        with tempfile.TemporaryDirectory() as root, unittest.mock.patch.object(hwshell, "can_enter", CAN_ENTER):
            root = Path(os.path.realpath(root))
            home, repo = root / "home", root / "repo"
            for folder in (home / "sub" / "deep", home / "closed", repo):
                folder.mkdir(parents=True)
            (home / "afile").write_text("x\n")
            (home / "link").symlink_to("sub/deep")
            (home / "closed").chmod(0o600)  # a folder that is there and cannot be entered
            guard = lambda line: pt.bash_problem(line, str(home), [home], [repo])
            writes = ("touch ../repo/x.txt", "rm ../repo/a.py", "echo hi > ../repo/x.txt", "cp afile ../repo/x.txt", "sed -i '' s/x/y/ ../repo/a.py",
                      "dd if=afile of=../repo/x.txt", "find ../repo -name a.py -delete", "touch n.txt")
            gits = ("git -C ../repo tag evil", "git -C ../repo commit -q --allow-empty -m evil", "git commit -m x", "git status", f"git -C {repo} log -1")
            for target in ("no/pe", "afile/x", "closed/x", "no/../sub", "afile/../sub", "link/..", "sub/..", "sub", "link", "."):
                for join in ("; ", "\n", " || ", " || true; ", " && true; "):  # what follows runs whether or not the `cd` succeeded
                    for write in writes:
                        self.assertIn("relative to a directory the guard cannot know", guard(f"cd {home}/{target}{join}{write}"), (target, join, write))
                    for git in gits:
                        self.assertIn("would run in a directory the guard cannot know", guard(f"cd {home}/{target}{join}{git}"), (target, join, git))
            for target in ("no/pe", "afile/x", "closed/x", "no/../sub", "afile/../sub", "link/..", "sub/.."):  # no folder as the shell walks it, or a `..`
                for write, git in zip(writes, gits):
                    self.assertIn("relative to a directory the guard cannot know", guard(f"cd {home}/{target} && {write}"), (target, write))
                    self.assertIn("would run in a directory the guard cannot know", guard(f"cd {home}/{target} && {git}"), (target, git))
                for rest in ("; ls ../repo", f"; touch {home}/n.txt", " && cat afile | head -3"):  # reads, and a write by its full path
                    self.assertIsNone(guard(f"cd {home}/{target}{rest}"), (target, rest))
            for line in (f"cd {home}/link; cd ..; touch ../repo/x.txt", f"cd {home}/link && cd .. && touch ../repo/x.txt", f"cd ../home/no/../sub; touch ../repo/x.txt",
                         f"ls && cd {home}/sub && touch n.txt", f"mkdir -p {home}/new; cd {home}/new && touch n.txt", f"cd {home}/sub && ls; cd {home} && touch n.txt",
                         f"git mv sub e; cd sub; touch ../repo/x.txt", f"git -C {home} mv sub e; cd {home}/sub && touch ../../repo/x.txt"):
                self.assertIn("relative to a directory the guard cannot know", guard(line), line)
            for line in (f"cd {home}/sub && touch ../n.txt", f"cd {home}/sub && cd deep && echo hi > n.txt 2>&1 | cat", f"cd {home}/link && touch n.txt && git add n.txt",
                         f"cd sub && touch n.txt; ls; echo done > {home}/out.txt", f"cd {home} && git commit -q -m x && git log -1 | cat"):
                self.assertIsNone(guard(line), line)
            self.assertIn("outside Herdwright's repo", guard(f"cd {home}/sub && touch ../../repo/x.txt"))
            self.assertIn("outside Herdwright's repo", guard(f"cd {home}/link && touch ../../../repo/x.txt"))  # a write is resolved as the kernel does
            self.assertIn("don't `cd`", guard(f"cd {repo}; ls"))
            self.assertIn("this `cd`", guard("cd +1; touch ../repo/x.txt"))  # zsh: an entry of its directory stack, though a folder `+1` may be there

    def test_find_execdir_is_refused_and_a_path_in_another_case_is_the_same_path_where_the_disk_does_not_tell_them_apart(self):
        """`find <dir> -name f.txt -execdir touch ../../repo/x.txt \\;` ran its command in the folder of the file
        found, where the guard did not look; and on a default macOS disk `<tmp>/REPO` is the project repo `<tmp>/repo`."""
        for run in ("touch ../../code/proj/x.txt", "rm ../../code/proj/a.py", "git -C ../../code/proj tag evil", "ls"):
            for primary in ("-execdir", "-okdir"):
                self.assertIn(f"`find {primary}`", bash(f"find /hw-home/d -name f.txt {primary} {run} \\;"), run)
        with tempfile.TemporaryDirectory() as root:
            root = Path(os.path.realpath(root))
            (root / "repo").mkdir()
            if not (root / "REPO").exists():
                self.skipTest("this disk tells `REPO` from `repo`")
            guard = lambda line: pt.bash_problem(line, str(root), [root], [root / "repo"])
            for line in (f"touch {root}/REPO/x.txt", f"echo hi > {root}/Repo/x.txt", f"rm {root}/REPO/a.py", f"git -C {root}/REPO tag evil",
                         f"cd {root}/REPO && touch x.txt", "touch REPO/x.txt"):
                self.assertIsNotNone(guard(line), line)
            self.assertIsNotNone(pt.decide({"session_id": COORD, "tool_name": "Write", "tool_input": {"file_path": f"{root}/REPO/a.txt"}, "cwd": str(root)},
                                           holder=COORD, roots=[root], protected=[root / "repo"]))
            self.assertIsNone(guard(f"touch {root}/other/x.txt"))

    def test_a_path_through_one_the_line_itself_changes_cannot_be_checked(self):
        """The guard resolves every path before the line runs. A link made on the line, or a folder it moves,
        makes a later path point elsewhere: `ln -s /hw-home /hw-home/d/k; touch /hw-home/d/k/../x` writes outside
        /hw-home. A link's relative target is read from the folder that holds the link."""
        for line in ("ln -s /hw-home /hw-home/d/k; touch /hw-home/d/k/../x.txt", "ln -s /hw-home /hw-home/d/k && echo hi > /hw-home/d/k/../x.txt",
                     "mv /hw-home/a /hw-home/b; touch /hw-home/b/../../x.txt", "cp -R /tmp/tree /hw-home/copy; rm /hw-home/copy/l/../x.txt",
                     "ln -sfn /hw-home/profile bin; touch bin/../x.txt"):
            self.assertIn("an earlier command of this line moves, copies or links", bash(line) or "", line)
        self.assertIn("would run in a directory the guard cannot know", bash("rsync -a /tmp/tree/ /hw-home/t; git -C /hw-home/t/l/.. commit -m x"))
        for line in ("rm -rf /hw-home/d; cd /hw-home/d; touch ../x.txt", "mv bin bin2; cd bin; touch ../../x.txt",
                     "ln -sfn /hw-home/profile /hw-home/l; cd /hw-home/l; touch ../x.txt", "chmod 000 bin; cd bin; rm ../x"):
            self.assertIn("relative to a directory the guard cannot know", bash(line) or "", line)
        for line in ("ln -s ../../code/proj /hw-home/d/l", "ln -s ../code/proj l", "cd /hw-home/bin && ln -s ../../code/proj/a.py l",
                     "ln -s -f ../../code/proj d/l", "ln /code/proj/a.py /tmp/hard", "ln -s -t /hw-home/d ../../code/proj", "ln -s /code/proj"):
            self.assertIn("outside Herdwright's repo", bash(line) or "", line)
        # what makes a plain file or folder, or names the changed path itself, changes nothing about where a path points
        for line in ("mkdir -p /hw-home/d && touch /hw-home/d/x.txt", "rm -f /tmp/out.txt; echo hi > /tmp/out.txt",
                     "rm -rf /tmp/d && mkdir -p /tmp/d && cp a.txt /tmp/d/a.txt && git -C /tmp/d init -q", "chmod +x /tmp/run.sh && /tmp/run.sh > /tmp/run.sh.out", "cp a.txt b.txt && echo x >> b.txt",
                     "ln -s notes.md l && touch other.txt", "mv /tmp/a /tmp/b && cat /tmp/b/x", "ln -s notes.md bin/l",
                     "ln -s ../notes.md bin/l",  # read from the link's folder it stays inside; `../notes.md` read from the shell does not
                     "cp /code/proj/a.py /tmp/copy.py"):  # a copy only reads what it copies
            self.assertIsNone(bash(line), line)

    def test_a_program_behind_a_wrapper_the_guard_lists_is_read_and_a_sed_script_that_may_write_is_refused(self):
        """`taskpolicy -b touch <repo>/x`, `lockf <file> touch <repo>/x` and `sed -n 'w <repo>/x' f` wrote.
        The guard read sed's language to find the file after `w` and read two scripts differently from sed; now
        a script that holds a `w` or an `e` outside a plain form is refused, wherever it would write."""
        for line in ("taskpolicy -b touch {}", "taskpolicy -c utility -d throttle rm {}", "lockf /tmp/l touch {}", "lockf -k -t 5 /tmp/l rm {}",
                     "lockf {} true", "flock -n /tmp/l touch {}", "flock --timeout=5 {} true", "chronic -v touch {}",
                     "sandbox-exec -p '(version 1)' touch {}", "sandbox-exec -D K=V -f /tmp/p.sb rm {}"):
            self.assertIn("outside Herdwright's repo/profile/tmp", bash(line.format("/code/proj/x.txt")) or "", line)
            self.assertIsNone(bash(line.format("/tmp/x.txt")), line)
        for line in ("sed -n 'w {}' /tmp/a", "sed 's/x/y/w {}' /tmp/a", "sed -n -e p -e '/x/w {}' /tmp/a", "sed -ne '1,3{{s|a|b|gw {}\n}}' /tmp/a",
                     "sed --expression='W {}' /tmp/a", "sed -i '' 's/a/b/w {}' /tmp/a", "sed -i 's/a/b/w {}' /tmp/a",
                     "sed -n '1a\\\nhello\\\\\nw {}' /tmp/a", "sed -n 'i\\\nfoo\\\\\nw {}' /tmp/a", "sed 's/[/]/g;/w {}' /tmp/a", "sed '1e touch {}' /tmp/a"):
            for target in ("/code/proj/x.txt", "/tmp/x.txt"):
                self.assertIn("this `sed`", bash(line.format(target)) or "", line)
        for line in ("sed -n '10,20p' /code/proj/a.py", "sed 's/wip/done/; /warn/d' /code/proj/a.py", "sed -E -n '/^## /,$p' /code/proj/README.md",
                     "sed -e :a -e '$!N;s/\\n//;ta' /code/proj/a.py", "sed 'y/abc/xyz/;5q' /code/proj/a.py", "sed '1!G;h;$!d' /code/proj/a.py",
                     "sed -n '/^## Findings/,/^## Cases/p' /tmp/review.md > /tmp/findings.txt", "sed -i '' 's/were/was/' /tmp/a"):
            self.assertIsNone(bash(line), line)

    def test_a_cd_before_a_write_is_followed(self):
        for command in ("cd /tmp && cd ../code/proj && touch c.txt", "cd /tmp; cd ../code/proj; rm -rf src", "cd /code && cd proj && echo x > c.txt",
                        "cd /tmp\ncd ../code/proj\ngit commit -m x", "cd ../code/proj && sed -i '' s/a/b/ c.txt"):
            self.assertIsNotNone(bash(command), command)
        for command in ("cd /tmp && touch c.txt", "cd bin && touch ../notes.md", "cd /tmp && cd /hw-home/profile && echo x > c.txt"):
            self.assertIsNone(bash(command), command)
        # only `cd <path> &&` at the head of the line is followed: after any other `cd` a relative write is refused
        for command in ("cd /tmp; cd /hw-home/profile; echo x > c.txt", "cd bin; touch notes.md", "ls && cd bin && touch notes.md",
                        "cd bin && ls; touch notes.md", "cd bin || touch notes.md", "cd bin\ntouch notes.md", "cd bin/.. && touch notes.md"):
            self.assertIn("relative to a directory the guard cannot know", bash(command), command)
        # from a project repo (OpenCode's workdir), a cd into Herdwright's home makes the write Herdwright's own
        self.assertIsNone(decide("shell", command="cd /hw-home && touch c.txt", workdir="/code/proj"))
        self.assertIsNotNone(decide("shell", command="cd src && touch c.txt", workdir="/code/proj"))
        # no other way to change directory is followed, so each is refused whatever comes after it
        for command in ("cd $D && touch /tmp/c.txt", "cd - && git status", "pushd /tmp && ls", "chdir /tmp", "true | cd /tmp; touch c.txt",
                        "(cd /hw-home) && touch c.txt", "builtin cd /tmp"):
            self.assertIsNotNone(bash(command), command)

    def test_an_opener_or_a_wrapper_before_a_write_does_not_hide_it(self):
        """Only the first word of a simple command was read as the program."""
        for command in ("command touch c.txt", "env touch c.txt", "env FOO=1 rm c.txt", "sudo rm keep.txt", "time rm keep.txt",
                        "nice -n 5 touch c.txt", "FOO=1 nohup git commit -m x", "exec -a n touch c.txt", "env -P /usr/bin touch c.txt",
                        # `timeout`, a run of operators, a `find` with options before its paths
                        "timeout 5 rm c.txt", "timeout -k 1 5s rm c.txt", "stdbuf -oL touch c.txt", "true;>c.txt", "find -L . -name x -delete"):
            self.assertIsNotNone(decide("shell", command=command, workdir="/code/proj"), command)
            self.assertIsNone(bash(command), command)  # in Herdwright's own repo
        # a compound command's opener is outside the grammar, wherever it would write
        for command in ("{ touch c.txt; }", "if true; then touch c.txt; fi", "for f in a b; do rm c.txt; done", "! rm keep.txt"):
            self.assertIn("as a command: the shell's own syntax", bash(command), command)
        for command in ("find -EX /code/proj -name x -delete", "find -EXd -- /code/proj -delete", "find -sEf /code/proj -exec rm {} ;"):
            self.assertIsNotNone(bash(command), command)  # BSD's grouped options hid the project path, so "." was checked
        for command in ("{ cd /code/proj && touch c.txt; }", "if true; then cd /code/proj; touch c.txt; fi", "builtin cd /code/proj && touch c.txt",
                        "cd -P /code/proj", "pushd /code/proj", "cd"):
            self.assertIsNotNone(bash(command), command)
        self.assertIn("it silently changes where later commands run", bash("cd /code/proj"))
        self.assertIsNone(decide("shell", command="env FOO=1 ls", workdir="/code/proj"))

    def test_every_redirect_and_pipe_operator_is_read(self):
        for command in ("echo x > c.txt", "echo x >> c.txt", "echo x 2> c.txt", "echo x 2>> c.txt", "> c.txt", "ls 2>&1 > c.txt",
                        "echo x > /tmp/ok 2> c.txt", "ls | cat > c.txt"):
            self.assertIn("outside Herdwright's repo/profile/tmp", decide("shell", command=command, workdir="/code/proj"), command)
            self.assertIsNone(bash(command), command)
        for command in ("echo x >&2", "ls 2>&1 | cat", "echo x > /tmp/out 2>&1", "cat < c.txt"):
            self.assertIsNone(decide("shell", command=command, workdir="/code/proj"), command)
        # every other spelling is outside the grammar (`&>`, `>&`, `|&`, zsh's `>>|`, `&>|`, `>!`)
        for command in ("echo x &> c.txt", "echo x &>> c.txt", "echo x >& c.txt", "ls |& tee c.txt", "echo x >>| c.txt", "echo x &>| c.txt",
                        "echo x >&| c.txt", "echo x >! c.txt", "echo x >| c.txt", "echo x <> c.txt"):
            self.assertIn("reads only plain command lines", bash(command), command)

    def test_writes_that_are_not_a_file_writers_first_word(self):
        """A heredoc with the redirect after it, git clean and checkout, chmod, dd and a deleting find passed."""
        for command in ("git clean -fd", "git checkout -- .", "git switch main", "chmod +x c.txt",
                        "chown bob c.txt", "dd if=/dev/zero of=c.txt", "find . -delete", "find src -exec rm {} \\;"):
            self.assertIsNotNone(decide("shell", command=command, workdir="/code/proj"), command)
            self.assertIsNone(bash(command), command)
        for command in ("find . -name '*.py'", "find . -exec grep -l x {} \\;", "chmod +x /tmp/run.sh", "dd if=c.txt of=/tmp/c", "git log"):
            self.assertIsNone(decide("shell", command=command, workdir="/code/proj"), command)

    def test_an_edit_that_names_no_file_is_denied_to_the_lock_holder(self):
        """hw-deny denies it; here an unknown input shape (a patch without file markers) passed silently."""
        for tool, inp in (("patch", {"patchText": "*** Begin Patch\n+x\n*** End Patch"}), ("patch", {"diff": "--- a\n+++ b"}),
                          ("write", {"filePath": "/code/proj/a.py", "content": "x"}), ("Edit", {})):
            self.assertIn("names no file", decide(tool, **inp), (tool, inp))
            self.assertIsNone(decide(tool, sid="ses_worker", **inp))  # not the lock holder

    def test_a_here_document_is_refused_and_its_text_goes_in_quotes(self):
        """The shell joins a backslash-newline inside a here-document, so its end line was read wrong and the
        command after it ran unseen. A here-document is outside the grammar; text of several lines goes in quotes."""
        for command in ("cat > /tmp/a.md <<'EOF'\ncd /code/proj\ngit push\nEOF\nls", "cat <<EOF\nEO\\\nF\ntouch /code/proj/x\nEOF",
                        "cat <<'EOF'\nx\nEOF\ntouch /code/proj/x", "bash <<< 'touch /code/proj/x'"):
            self.assertIn("a here-document or here-string (write the text in quotes", bash(command), command)
        self.assertIsNone(bash("bin/hw log T1 \"line one\nline two; cd /code/proj && git push\" kind=note"))
        self.assertIsNone(bash("echo 'cd /code/proj\ngit push' > /tmp/a.md"))

    def test_subagents(self):
        self.assertIn("bin/hw spawn", decide("Agent", prompt="x"))
        self.assertIsNotNone(decide("Workflow"))
        self.assertIsNone(pt.decide({"session_id": COORD, "tool_name": "Agent", "tool_input": {}}, holder=COORD, roots=ROOTS, allow_subagents=True, protected=[]))

    def test_opencode_tool_names_and_inputs_are_checked_like_claudes(self):
        self.assertIn("spawn a worker", decide("edit", path="/code/proj/a.py", oldString="a", newString="b"))
        self.assertIn("spawn a worker", decide("write", path="/code/proj/a.py", content="x"))
        self.assertIsNone(decide("write", path="/hw-home/profile/briefs/T1.md", content="x"))
        patch = "*** Begin Patch\n*** Update File: /hw-home/README.md\n*** Add File: /code/proj/new.py\n+x\n*** End Patch"
        self.assertIn("/code/proj/new.py is outside", decide("patch", patchText=patch))
        self.assertIsNone(decide("patch", patchText="*** Begin Patch\n*** Update File: /hw-home/README.md\n*** End Patch"))
        self.assertIn("git commit", decide("shell", command="git commit -m x", workdir="/code/proj"))
        self.assertIn("git commit", decide("shell", command="git commit -m x", workdir="../code/proj", cwd="/hw-home"))
        self.assertIsNone(decide("shell", command="git commit -m x"))  # in Herdwright's own repo
        self.assertIsNone(decide("shell", command="bin/hw status"))
        self.assertIn("bin/hw spawn", decide("subagent", description="x", prompt="y"))
        for tool in ("read", "grep", "glob", "skill"):
            self.assertIsNone(decide(tool, path="/code/proj/a.py", pattern="x", id="relay"))
        self.assertIsNone(decide("write", sid="ses_worker", path="/code/proj/a.py", content="x"))  # not the lock holder

    def test_the_canary_is_denied_in_every_session(self):
        for tool, sid, holder in (("shell", "ses_new", COORD), ("Bash", COORD, COORD), ("shell", "ses_new", None)):
            reason = decide(tool, sid=sid, holder=holder, command=" hw-pretool-canary 1f2e3d4c")
            self.assertIn("canary: `hw-pretool-canary 1f2e3d4c` is denied in every session", reason)
        self.assertIsNone(decide("shell", sid="ses_new", command="echo hw-pretool-canary"))
        self.assertIsNone(decide("read", sid="ses_new", path="hw-pretool-canary"))

    def test_hook_json_and_fail_open(self):
        with tempfile.TemporaryDirectory() as d:
            payload = json.dumps({"session_id": "s", "tool_name": "Write", "tool_input": {"file_path": "/code/x"}})
            # no lock held by "s" in the real profile: allowed, no output
            r = subprocess.run([str(_load.HOME / "bin" / "hw-pretool")], input=payload, capture_output=True, text=True, env={"HW_PROFILE_DIR": d, "PATH": "/usr/bin:/bin"})
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            (Path(d) / ".cache").mkdir()
            (Path(d) / ".cache" / "hw.lock").write_text(json.dumps({"agent_session": "s"}))
            r = subprocess.run([str(_load.HOME / "bin" / "hw-pretool")], input=payload, capture_output=True, text=True, env={"HW_PROFILE_DIR": d, "PATH": "/usr/bin:/bin"})
            out = json.loads(r.stdout)["hookSpecificOutput"]
            self.assertEqual((r.returncode, out["permissionDecision"]), (0, "deny"))
            self.assertIn("hw-pretool:", out["permissionDecisionReason"])
            bad = subprocess.run([str(_load.HOME / "bin" / "hw-pretool")], input="not json", capture_output=True, text=True)
            self.assertEqual((bad.returncode, bad.stdout), (0, ""))


@unittest.skipUnless(shutil.which("node"), "node is needed to run the OpenCode plugin")
class CoordinatorPluginTests(unittest.TestCase):
    """The coordinator's generated plugin, run for real under node with the hook object OpenCode v2.2.7 calls it with."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.profile = self.root / "profile"
        (self.profile / ".cache").mkdir(parents=True)
        (self.profile / ".cache" / "hw.lock").write_text(json.dumps({"agent_session": "ses_coord"}))
        stub = self.root / "cfg" / "node_modules" / "@opencode" / "plugin"  # OpenCode supplies this package itself
        stub.mkdir(parents=True)
        (stub / "package.json").write_text('{"name": "@opencode/plugin", "type": "module", "main": "index.js"}')
        (stub / "index.js").write_text("export const Plugin = { define: (definition) => definition };\n")
        (self.root / "cfg" / "package.json").write_text('{"type": "module"}')

    def before(self, tool, tool_input, session="ses_coord", hook=_load.HOME):
        plugin = hwdeny.coordinator_plugin(self.root / "cfg", hook, {"HW_PROFILE_DIR": str(self.profile)})
        plugin = plugin / "plugins" / "hw-pretool" / "index.js"
        script = (f'import plugin from {json.dumps(plugin.as_uri())};\n'
                  f'const hooks = {{}};\n'
                  f'await plugin.setup({{ location: {{ directory: {json.dumps(str(_load.HOME))} }}, '
                  f'tool: {{ hook: async (name, callback) => {{ hooks[name] = callback; }} }} }});\n'
                  f'try {{ await hooks["execute.before"]({{ tool: {json.dumps(tool)}, sessionID: {json.dumps(session)}, agent: "build", '
                  f'input: {json.dumps(tool_input)} }}); console.log(plugin.id + " allowed"); }}\n'
                  f'catch (e) {{ console.log("blocked: " + e.message); }}\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith("HW_")}
        r = subprocess.run(["node", "--input-type=module", "-e", script], env=env, capture_output=True, text=True)
        return r.stdout.strip() or r.stderr.strip()

    def test_the_lock_holder_is_denied_an_edit_in_a_project_repo_and_allowed_its_own_work(self):
        self.assertIn("blocked: hw-pretool: /code/proj/a.py is outside Herdwright's repo", self.before("write", {"path": "/code/proj/a.py", "content": "x"}))
        self.assertIn("blocked: hw-pretool: `git push`", self.before("shell", {"command": "git -C /code/proj push"}))
        self.assertEqual(self.before("shell", {"command": "bin/hw status"}), "hw.pretool allowed")
        self.assertEqual(self.before("write", {"path": str(self.profile / "briefs" / "T1.md"), "content": "x"}), "hw.pretool allowed")

    def test_another_session_passes_and_the_canary_is_denied_to_any(self):
        self.assertEqual(self.before("write", {"path": "/code/proj/a.py", "content": "x"}, session="ses_worker"), "hw.pretool allowed")
        self.assertIn("blocked: hw-pretool: canary: `hw-pretool-canary ab12`", self.before("shell", {"command": "hw-pretool-canary ab12"}, session="ses_new"))

    def test_a_guard_that_cannot_run_denies(self):
        self.assertIn("blocked: ", self.before("shell", {"command": "ls"}, hook=self.root / "no-such-hw-home"))


if __name__ == "__main__":
    unittest.main()
