"""Command lines for the grammar the guards read (hwshell.GRAMMAR), shared by the tests of hwshell, hw-pretool and
hw-deny: what it reads, each construct it refuses, a generator that builds lines from the refused constructs, and a
table of the forms the grammar reads around commands that write (`form_lines`)."""
import itertools
import random

# (command line, its tokens): one or more per rule of the grammar. Operators are the bare `;` `&&` `||` `|` `>` `>>` `<`.
READS = [
    ("bin/hw status", ["bin/hw", "status"]),                                               # plain words
    ("git log --format=%h -n5 HEAD~2 main@{1} a,b +x k=v", ["git", "log", "--format=%h", "-n5", "HEAD~2", "main@{1}", "a,b", "+x", "k=v"]),
    ("echo 'a b' \"c d\" e'f'\"g\"", ["echo", "a b", "c d", "efg"]),                       # quotes
    ("echo '$(x) `y` ; | > $z' \"a;b|c>d (e) {f,g} #h\"", ["echo", "$(x) `y` ; | > $z", "a;b|c>d (e) {f,g} #h"]),
    ("echo \"a\\\"b\\$c\\\\d\" \\$e \\; \\#", ["echo", "a\"b$c\\d", "$e", ";", "#"]),         # a backslash before a character
    ("echo $HOME \"${USER}x\" $? \"cost: 5$\" a$", ["echo", "$HOME", "${USER}x", "$?", "cost: 5$", "a$"]),  # `$NAME`, `${NAME}`
    ("a | b && c || d; e\nf", ["a", "|", "b", "&&", "c", "||", "d", ";", "e", ";", "f"]),  # how commands are joined
    ("a > b >> c < d 2> e 2>> f", ["a", ">", "b", ">>", "c", "<", "d", ">", "e", ">>", "f"]),  # redirects to a file
    ("a 2>&1 >&2 1>&2 | b", ["a", "|", "b"]),                                              # ... and between descriptors
    ("a \\\n b 'c\\\nd' \"e\\\nf\"", ["a", "b", "c\\\nd", "ef"]),                           # a backslash at the end of a line
    ("cd /tmp && ls *.md ~/x ~", ["cd", "/tmp", "&&", "ls", "*.md", "~/x", "~"]),
    ("find . -name '*.py' -exec grep -l x {} +", ["find", ".", "-name", "*.py", "-exec", "grep", "-l", "x", "{}", "+"]),
    ("echo 'ünï' \"—\" '' \"\"", ["echo", "ünï", "—", "", ""]),
]

# (what stands on the command line, a piece of the refusal): every construct outside the grammar. `{w}` is a command.
REFUSED = [
    ("cat <<EOF\n{w}\nEOF", "here-document"), ("cat <<'EOF'\nx\nEOF\n{w}", "here-document"), ("cat <<-EOF\n\tx\n\tEOF", "here-document"),
    ("bash <<< '{w}'", "here-string"), ("cat <<EOF\nEO\\\nF\n{w}\nEOF", "here-document"),
    ("echo $({w})", "`$( )`"), ("echo \"$({w})\"", "`$( )`"), ("x=$({w})", "`$( )`"), ("echo `{w}`", "backquoted"), ("echo \"`{w}`\"", "backquoted"),
    ("cat <({w})", "`<(`"), ("ls > >({w})", "`>(`"), ("({w})", "`(` outside quotes"), ("ls | ({w})", "`(` outside quotes"),
    ("f() {{ {w}; }}; f", "`(` outside quotes"), ("a=(1 2)", "`(` outside quotes"), ("ls *(.)", "`(` outside quotes"),
    ("(( x = 1 << 2 ))\n{w}", "`(` outside quotes"), ("echo $(( 1 + 2 ))", "only `$NAME` and `${NAME}`"), ("echo $(({w}); (true))", "only `$NAME`"),
    ("echo ${{x:-$({w})}}", "only `$NAME`"), ("echo ${{x:=y}}", "only `$NAME`"), ("echo ${{x::=y}}", "only `$NAME`"), ("echo ${{#x}} ${{x[1]}}", "only `$NAME`"),
    ("echo $1 $@", "only `$NAME`"), ("echo $'a\\tb'", "only `$NAME`"), ("echo $\"a\"", "only `$NAME`"), ("echo $x[1]", "subscript"), ("echo ${{x", "only `$NAME`"),
    ("{w} &", "`&`"), ("{w} & ls", "`&`"), ("ls |& cat", "`|&`"), ("ls &> /tmp/x", "`&>`"), ("ls &>> /tmp/x", "`&>`"), ("a ;; b", "`;;`"), ("a ;& b", "`;&`"),
    ("echo x >| /tmp/x", "`>|`"), ("echo x >! /tmp/x", "`>!`"), ("echo x >>| /tmp/x", "`>>|`"), ("echo x >>! /tmp/x", "`>>!`"),
    ("echo x &>| /tmp/x", "`&>`"), ("echo x >&| /tmp/x", "`>&`"), ("echo x >& /tmp/x", "`>&`"), ("echo x >&- ", "`>&`"),
    ("echo x <> /tmp/x", "`<>`"), ("cat <&3", "`<&`"), ("echo x 3> /tmp/x", "glued to a word"), ("echo x {{fd}}> /tmp/x", "glued to a word"),
    ("echo x>/tmp/x", "glued to a word"), ("ls <1-5>", "glued to a word"), ("echo x >", "without its file"), ("echo x > ; ls", "without its file"),
    ("echo x > $F", "must be written out"), ("echo x > \"$D/f\"", "must be written out"), ("echo x > /tmp/*.txt", "must be written out"),
    ("ls # {w}", "comment"), ("# {w}\nls", "comment"), ("touch /tmp/{{a,b}}", "brace expansion"), ("touch /tmp/x{{1..3}}", "brace expansion"),
    ("=touch /tmp/x", "begins with `=`"), ("rm ~bob/x", "only `~` and `~/...`"), ("cd ~+", "only `~` and `~/...`"),
    ("echo 'unclosed", "unclosed single quote"), ("echo \"unclosed", "unclosed double quote"), ("echo x \\", "backslash at the end"),
    ("echo a\x0bb", "outside quotes"), ("echo café", "outside quotes"), ("echo a b", "outside quotes"), ("echo a\rb", "outside quotes"),
    # the shell's own syntax and the commands whose effect cannot be read from their words
    ("if true; then {w}; fi", "`if` as a command"), ("for f in a b; do {w}; done", "`for` as a command"), ("while true; do {w}; done", "`while` as a command"),
    ("until false; do {w}; done", "`until` as a command"), ("case x in x) {w};; esac", "outside quotes"), ("function f {{ {w}; }}; f", "`function` as a command"),
    ("{{ {w}; }}", "`{` as a command"), ("! {w}", "`!` as a command"), ("[[ -n 1 ]] && {w}", "`[[` as a command"), ("if [[ -n 1 ]] {w}", "`if` as a command"),
    ("repeat 2 {w}", "`repeat` as a command"), ("coproc {w}", "`coproc` as a command"), ("select x in a; do {w}; done", "`select` as a command"),
    ("eval '{w}'", "`eval` runs text"), ("command eval '{w}'", "`eval` runs text"), ("builtin eval {w}", "`eval` runs text"), ("source /tmp/x.sh", "`source` runs text"),
    (". /tmp/x.sh", "`.` runs text"), ("trap '{w}' EXIT", "`trap` runs text"), ("emulate sh -c '{w}'", "`emulate` runs text"),
    ("script -q /dev/null {w}", "`script` runs text"), ("alias g='{w}'", "`alias` changes which program"), ("hash -p /usr/bin/touch git", "`hash` changes which program"),
    ("hash t=/usr/bin/touch", "`hash` changes which program"), ("enable -f /tmp/x.so x", "`enable` changes which program"), ("zmodload zsh/files", "`zmodload` changes which program"),
    ("zcompile /tmp/x", "`zcompile` changes which program"), ("set -a", "`set` changes how the shell"), ("setopt allexport", "`setopt` changes how the shell"),
    ("shopt -s expand_aliases", "`shopt` changes how the shell"), ("zstyle :x y z", "`zstyle` changes how the shell"), ("pushd /tmp", "`pushd` changes directory"),
    ("popd", "`popd` changes directory"), ("chdir /tmp", "`chdir` changes directory"), ("print -v X y", "`print` sets a variable"), ("let x=1", "`let` sets a variable"),
    ("history -w /tmp/x", "`history` sets a variable"), ("getopts a x", "`getopts` sets a variable"),
    ("sh -c '{w}'", "read only as `sh <script file>"), ("bash -lc '{w}'", "read only as `bash <script file>"), ("bash", "read only as `bash <script file>"),
    ("zsh -s", "read only as `zsh <script file>"), ("echo '{w}' | bash", "read only as `bash <script file>"), ("bash /dev/stdin", "read only as `bash <script file>"),
    ("bash -x /tmp/run.sh", "read only as `bash <script file>"), ("csh -c '{w}'", "read only as `csh <script file>"), ("xargs sh -c '{w}'", "read only as `sh <script file>"),
    ("env sh $X", "read only as `sh <script file>"), ("bash /tmp/*.sh", "read only as `bash <script file>"),
    ("cd", "this `cd`"), ("cd -", "this `cd`"), ("cd -q /tmp", "this `cd`"), ("cd -P /tmp", "this `cd`"), ("cd /tmp /var", "this `cd`"), ("cd $D", "this `cd`"),
    ("cd /tm*", "this `cd`"), ("ls | cd /tmp", "this `cd`"), ("cd /tmp | ls", "this `cd`"), ("builtin cd /tmp", "this `cd`"), ("command cd /tmp", "this `cd`"),
    ("X=1 cd /tmp", "this `cd`"), ("cd /tmp > /tmp/out", "this `cd`"), ("cd /tmp/^x", "this `cd`"),
    ("$X /tmp/x", "which program runs"), ("\"$X\"", "which program runs"), ("*", "which program runs"), ("env $X touch /tmp/x", "which program runs"),
    ("nice -n $N touch /tmp/x", "which program runs"), ("env -u * touch /tmp/x", "which program runs"), ("functions[f]='{w}'", "which program runs"),
    ("env -S '{w}'", "an option of `env`"), ("env -C /tmp {w}", "an option of `env`"), ("env --chdir=/tmp {w}", "an option of `env`"), ("sudo -D /tmp {w}", "an option of `sudo`"),
    ("sudo -s", "an option of `sudo`"), ("nice --foo 5 {w}", "an option of `nice`"), ("timeout --kill-after 1 5 {w}", "an option of `timeout`"),
    ("xargs --max-args 1 {w}", "an option of `xargs`"), ("exec -z {w}", "an option of `exec`"), ("command -x {w}", "an option of `command`"),
    ("rm $F", "read word by word"), ("rm -rf /tmp/x/*", "read word by word"), ("touch \"$D/x\"", "read word by word"), ("cp a* /tmp", "read word by word"),
    ("sed -i s/a/b/ *.md", "read word by word"), ("find $D -delete", "read word by word"), ("find . -name *.py", "read word by word"), ("dd if=a of=$F", "read word by word"),
    ("export $N=1", "read word by word"), ("declare -x \"$N\"", "read word by word"), ("read $N", "read word by word"), ("ls | xargs rm", "read word by word"),
    ("ls | xargs -I{{}} touch {{}}/x", "read word by word"), ("ls | xargs find", "read word by word"),
    ("find -files0-from /tmp/list -delete", "`find -files0-from`"),
    # a word that is no program's name before the command, builtins that run or load a command, sed scripts
    ("- {w}", "where a program's name stands"), ("+x", "where a program's name stands"), ("%1", "where a program's name stands"),
    ("@x {w}", "where a program's name stands"), ("flock /tmp/l -c '{w}'", "where a program's name stands"),
    ("typeset -fu zargs", "a function option"), ("declare -f f", "a function option"), ("local +f f", "a function option"), ("export -f f", "a function option"),
    ("compgen -C '{w}'", "`compgen` runs text"), ("complete -C '{w}' ls", "`complete` runs text"), ("bind -x '\"a\": {w}'", "`bind` runs text"),
    ("zle -N x", "`zle` runs text"), ("r", "`r` runs text"), ("compctl -K f ls", "`compctl` runs text"), ("apply '{w}' a", "`apply` runs text"),
    ("chroot / {w}", "`chroot` changes directory"), ("sed -f /tmp/s.sed /tmp/a", "this `sed`"), ("sed '1e {w}' /tmp/a", "this `sed`"),
    ("sed s/a/b/e /tmp/a", "this `sed`"), ("sed --expression-file=x /tmp/a", "this `sed`"),
    ("taskpolicy -p 1 {w}", "an option of `taskpolicy`"), ("lockf -x /tmp/l {w}", "an option of `lockf`"), ("flock -c '{w}' /tmp/l", "an option of `flock`"),
    ("sandbox-exec -x {w}", "an option of `sandbox-exec`"), ("chronic -x {w}", "an option of `chronic`"),
    # a `cd` path that is no path to zsh, builtins that run a command per line, `find -execdir`, sed's `w`
    ("cd +1", "this `cd`"), ("cd -1", "this `cd`"), ("cd ~", "this `cd`"), ("cd @x", "this `cd`"),
    ("mapfile -C '{w}' -c 1 X", "`mapfile -C`"), ("readarray -c 1 -C '{w}' X", "`readarray -C`"), ("mapfile -tC '{w}' X", "`mapfile -tC`"),
    ("find /tmp -name x -execdir {w} \\;", "`find -execdir`"), ("find /tmp -okdir {w} \\;", "`find -okdir`"),
    ("sed -n 'w /tmp/x' /tmp/a", "this `sed`"), ("sed 's/a/b/w /tmp/x' /tmp/a", "this `sed`"),
    ("sed -n '1a\\\nhello\\\\\nw /tmp/x' /tmp/a", "this `sed`"), ("sed 's/[/]/g;/w /tmp/x' /tmp/a", "this `sed`"),
]
WRITES = ("true", "touch /code/proj/x.txt", "git -C /code/proj tag evil")
AROUND = ("{c}", "ls; {c}", "ls && {c}", "true || {c}", "ls\n{c}", "{c}\nls", "{c}; echo done", "echo \\\n ok; {c}")
# lines that keep to the grammar, to put around a refused construct
PLAIN = ("ls", "bin/hw status", "git status", "echo 'a b' \"$HOME\"", "cat /tmp/a.txt | head -3", "touch /tmp/n.txt", "echo x > /tmp/out.txt 2>&1")


def refused_lines(seed=6, extra=300):
    """Command lines built from the refused constructs: each one with each write put in it, in each place of AROUND,
    and `extra` more with a construct at a random place among plain commands (the same every run: `seed`)."""
    for construct, _ in REFUSED:
        for write, around in itertools.product(WRITES, AROUND) if "{w}" in construct else [("", "{c}")]:
            yield around.format(c=construct.format(w=write))
    rng = random.Random(seed)
    anywhere = [construct for construct, _ in REFUSED if not construct.endswith("\\")]  # a `\` is refused only at the very end
    for _ in range(extra):
        commands = [rng.choice(PLAIN) for _ in range(rng.randint(0, 4))]
        commands.insert(rng.randint(0, len(commands)), rng.choice(anywhere).format(w=rng.choice(WRITES)))
        line = commands[0]
        for command in commands[1:]:
            line += rng.choice(("; ", " && ", " || ", "\n", " ;\n")) + command
        yield line


# Forms that keep to the grammar, around a command `{w}`: run from a folder {home} that holds the file `afile`, the
# folders `sub`, `+1` and `kept` (tracked by git) and `link`, a link to `sub`; {parent} holds {home}. With `form_lines` they make the second table: what
# a guard allows of it is run for real and must leave the repo as it was (real_shell.Scratch).
FORMS = (
    "{w}", "ls; {w}", "ls && {w}", "false || {w}", "ls | {w}", "X=1 {w}", "{w} 2>&1 | cat", "{w} > {home}/out.txt", "ls\n{w}",
    # a `cd` that fails leaves the shell where it stood
    "cd {home}/no/pe; {w}", "cd {home}/no/pe || {w}", "cd {home}/no/pe\n{w}", "cd {home}/no/pe || true; {w}",
    "cd {home}/no/pe && true; {w}", "cd {home}/afile/x; {w}", "cd {home}/sub; {w}", "cd {home}/sub && {w}", "cd {home} && {w}",
    # ... as does one through a folder that is not there and `..`, which a resolved path no longer shows;
    # after a link the shell goes back to the folder that holds the link, the kernel to the one above its target
    "cd {home}/no/../sub; {w}", "cd {home}/afile/../sub; {w}", "cd {home}/no/../sub || true; {w}", "cd ../home/no/../sub; {w}",
    "cd {home}/no/../sub && true; {w}", "cd {home}/link; cd ..; {w}", "cd {home}/link/..; {w}", "cd {home}/link && cd .. && {w}",
    "cd {home}/+1; {w}", "cd +1; {w}", "cd {home}; cd +1; {w}", "cd {home}/sub && ls; {w}", "ls && cd {home}/sub && {w}",
    # ... and so does one to a path the line itself changes first
    "mkdir -p {home}/new/x; cd {home}/new/x; {w}", "rm -rf {home}/sub; cd {home}/sub/x; {w}", "mv {home}/sub {home}/sub2; cd {home}/sub/x; {w}",
    "chmod 000 {home}/sub; cd {home}/sub; {w}", "ln -s {parent} {home}/l; cd {home}/l; {w}", "ln -s {home}/../home {home}/h; cd {home}/h; {w}",
    "ln -sfn {home} {home}/link; cd {home}/link; {w}", "ln -sfn .. {home}/link; cd {home}/link; {w}",
    "git mv kept e; cd kept; {w}", "git -C {home} mv kept e; cd {home}/kept; {w}", "git rm -rq kept; cd kept; {w}",
    "rmdir {home}/sub; cd {home}/sub && true; {w}",
    # a word or a program before the command
    "- {w}", "true; - {w}", "command {w}", "env {w}", "env -i {w}", "nice {w}", "nohup {w}", "time {w}", "xargs {w}", "taskpolicy -b {w}",
    "taskpolicy -c utility {w}", "lockf {home}/lock {w}", "lockf -k {home}/lock {w}", "sandbox-exec -p '(version 1)(allow default)' {w}",
    "chronic {w}", "flock {home}/lock {w}", "chroot / {w}", "find {home} -maxdepth 0 -exec {w} \\;",
)
# Commands that write a project repo {repo} when run from {home}, its neighbour ...
PROJECT_WRITES = (
    "touch {repo}/x.txt", "touch ../repo/x.txt", "echo hi > ../repo/x.txt", "echo hi >> {repo}/a.py", "rm ../repo/a.py", "rm -f {repo}/a.py",
    "cp afile ../repo/b.py", "mkdir ../repo/d", "tee ../repo/x.txt", "dd if=afile of=../repo/x.txt", "find ../repo -name a.py -delete",
    "ln -s afile ../repo/l", "git -C ../repo tag evil", "git -C {repo} tag evil", "git -C ../repo commit -q --allow-empty -m evil",
    "sed -n 'w ../repo/x.txt' afile", "sed 's/x/y/w {repo}/x.txt' afile", "sed -i.bak s/x/y/ ../repo/a.py", "lockf ../repo/x.lock true",
    "mkdir {home}/d; ln -s ../../repo {home}/d/l; touch {home}/d/l/x.txt", "mkdir {home}/d && ln -s ../../repo {home}/d/l && echo hi > {home}/d/l/x.txt",
    "mkdir {home}/d; ln -s ../../repo {home}/d/l; cd {home}/d/l; touch x.txt", "ln -s {home} {home}/sub/k; touch {home}/sub/k/../repo/x.txt",
    "mv {home}/link {home}/m; touch {home}/m/../../repo/x.txt",
    # where `find -execdir` runs, a sed script read differently from sed, the repo's name in another case
    "find {home}/sub -maxdepth 0 -execdir touch ../repo/x.txt \\;", "find {home} -name afile -execdir git -C ../repo tag evil \\;",
    "sed -n '1a\\\nhello\\\\\nw ../repo/x.txt' afile", "sed 's/[/]/g;/w ../repo/x.txt' afile", "touch {parent}/REPO/x.txt",
    "ln -s ../../repo {home}/sub", "ln afile {repo}/hard",
    # a path behind a group of short options, a copy that links, a program option by its beginning
    "git format-patch -qo{repo}/p -1", "git config -zf{repo}/.git/config user.name evil", "cp -l {repo}/a.py {home}/h; echo hi > {home}/h",
    "cp -s {repo}/a.py {home}/s; echo hi > {home}/s", "git rebase --exe='touch {repo}/x.txt' HEAD~1", "git ls-remote --upload-pa={mark} {repo}",
    "git rebase -qx'touch {repo}/x.txt' HEAD~1",  # the program option inside a group of short options
)
# ... and commands that change the worktree {repo} of a hat that stands in it
WORKTREE_WRITES = (
    "touch x.txt", "touch {repo}/x.txt", "echo hi > a.py", "rm a.py", "cp a.py b.py", "sed -n 'w x.txt' a.py", "sed -i.bak s/x/y/ a.py",
    "git tag evil", "git commit -q --allow-empty -m x", "git branch evil", "git update-ref refs/heads/evil HEAD", "git config user.name evil",
    "git diff --output=a.py HEAD", "git worktree add -q {home}/wt", "git read-tree --empty", "git symbolic-ref HEAD refs/heads/other",
    "git stash drop -q", "git -C {repo} tag evil", "git --git-dir={repo}/.git tag evil", "git -c alias.ci=commit ci -q --allow-empty -m x",
    "GIT_DIR={repo}/.git git commit -q --allow-empty -m x", "export GIT_DIR={repo}/.git; git -C {home} tag evil",
    "env GIT_DIR={repo}/.git GIT_WORK_TREE={repo} git stash drop -q", "git clone -q {home} c", "ln -s {repo} {home}/w; touch {home}/w/x.txt",
    "ln -s {parent} {home}/w; touch {home}/w/repo/x.txt", "find . -maxdepth 0 -exec touch x.txt \\;", "ls a.py | xargs git log --output=x.txt",
    # git in a scratch clone that names a path in the worktree, or writes the repo behind it
    "git -C {clone} diff --output={repo}/a.py HEAD~1", "git -C {clone} log --output={repo}/a.py", "git -C {clone} archive -o {repo}/x.tar HEAD",
    "git -C {clone} format-patch -q -1 -o {repo}/p", "git -C {clone} bundle create -q {repo}/x.bundle HEAD", "git -C {clone} worktree add -q {repo}/w2",
    "git -C {clone} checkout-index -a -f --prefix={repo}/n/", "git -C {clone} init -q {repo}/sub", "git -C {home} init -q {repo}/sub",
    "git -C {clone} config --file {repo}/.git/config user.name evil", "git -C {clone} push -q origin HEAD:refs/heads/evil",
    "git -C {clone} push -q {repo} HEAD:refs/heads/evil", "git -C {clone} diff --output=../repo/a.py HEAD~1",
    "find {home}/sub -maxdepth 0 -execdir touch ../repo/x.txt \\;", "sed 's/[/]/g;/w x.txt' a.py", "touch {parent}/REPO/x.txt", "rm {parent}/REPO/a.py",
    # a path behind a group of short options, a copy that links, a program option by its beginning, a
    # `.git` file the line writes, `~` after the line set HOME, a patch that names a path outside the clone's folder
    "git -C {clone} format-patch -qo{repo}/p -1", "git -C {clone} format-patch -o{repo}/a=b -1", "git -C {clone} format-patch -qo{repo}/a=b -1",
    "git -C {clone} config -zf{repo}/.git/config user.name evil",
    "cp -l a.py {home}/h; echo hi > {home}/h", "cp -s {repo}/a.py {home}/s; echo hi > {home}/s",
    "git -C {clone} rebase --exe='touch {repo}/x.txt' HEAD~1", "git -C {clone} rebase -x'touch {repo}/x.txt' HEAD~1",
    "mkdir {home}/gf; echo 'gitdir: {repo}/.git' > {home}/gf/.git; git -C {home}/gf tag evil", "HOME={repo}; touch ~/x.txt",
    "export HOME={repo}; echo hi > ~/x.txt",
    # the program option inside a group of short options, `~` after `=` once the line set HOME (bash expands it there)
    "git -C {clone} rebase -qx'touch {repo}/x.txt' HEAD~1", "HOME={repo}; dd if=a.py of=~/x.txt",
)


# a value attached to a short option that holds a `/` is a message, a branch name or a path the guard can judge.
# In a repo the session may change ({clone}, with {home} beside it) each of these runs and leaves the project repo alone.
ATTACHED_VALUES = (
    'git -C {clone} commit -q --allow-empty -m"fix a/b"', 'git -C {clone} commit -q --allow-empty -am"fix a/b"',
    'git -C {clone} commit -q --allow-empty -m"see README, 1/2"', "git -C {clone} checkout -q -bfeat/x", "git -C {clone} switch -q -ctask/x",
    'git -C {clone} stash push -q -m"wip a/b"', "git -C {clone} commit -q --allow-empty -F{home}/afile", "git -C {clone} archive -o{home}/a.tar HEAD",
    "git -C {clone} format-patch -q -o{home}/patches -1", "git -C {clone} format-patch -qo{home}/patches -1",
    "git -C {clone} format-patch -q -opatches/sub -1",
    # behind a `/` further in, a name nobody running the line can write (a folder at the root) is no path
    "git -C {clone} checkout -q -bfeature/usr", "git -C {clone} switch -q -cuser/bin", 'git -C {clone} commit -q --allow-empty -m"docs/usr/bin"',
)


def form_lines(writes, forms=FORMS):
    """Every form with every write in it (a case for real_shell.Scratch.line)."""
    for form, write in itertools.product(forms, writes):
        yield form.replace("{w}", write)
