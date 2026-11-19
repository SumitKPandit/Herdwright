"""Run a command line for real in bash or zsh next to a scratch project repo and say whether it reached the repo: what
the guards' answers are held against (tests of hw-pretool and hw-deny)."""
import os
import shutil
import subprocess
from pathlib import Path

from probe_git_reads import probe_repo, repo_state


class Scratch:
    """Under `root`, the folder `top` with: `repo` (probe_repo: a project repo with history), `home` (a repo of its
    own with the file `afile`, the folders `sub`, `+1` and `kept`, which holds a file git tracks, and `link`, a link
    to `sub`), `clone` (a clone of `repo`, its `origin`) and `mark` (a program that leaves `ran` behind when anything
    runs it). `reaches` runs a line there and makes the folder fresh again."""

    def __init__(self, root):
        root = Path(os.path.realpath(root))
        self.template, self.top = root / "template", root / "top"
        self.template.mkdir()
        probe_repo(self.template)
        (self.template / "mark").write_text('#!/bin/sh\ntouch "$(dirname "$0")/ran"\n')
        subprocess.run("git init -q -b main home && mkdir home/kept && touch home/kept/f && git -C home add kept && "
                       "git -C home -c user.name=t -c user.email=t@t commit -q -m root && "
                       "git clone -q repo clone && git -C clone remote set-url origin ../repo",
                       shell=True, cwd=self.template, check=True)
        (self.template / "home" / "sub").mkdir()
        (self.template / "home" / "+1").mkdir()
        (self.template / "home" / "afile").write_text("x\n")
        (self.template / "home" / "link").symlink_to("sub")
        self.repo, self.home = self.top / "repo", self.top / "home"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        self.fresh()

    def fresh(self):
        if self.top.exists():
            subprocess.run(["chmod", "-R", "u+rwx", str(self.top)], capture_output=True)  # a line may have closed a folder
            shutil.rmtree(self.top)
        shutil.copytree(self.template, self.top, symlinks=True)

    def line(self, template):
        """A case's command line with its places filled in: {repo}, {home}, {clone}, {parent} (the folder that holds
        them) and {mark}."""
        return template.format(repo=self.repo, home=self.home, clone=self.top / "clone", parent=self.top,
                               mark=self.top / "mark").replace("\\n", "\n")

    def reaches(self, line, shell, cwd="home"):
        """Whether `line`, run for real by `shell` standing in the folder `cwd`, changes the project repo or runs
        `mark`."""
        before = repo_state(self.repo)
        subprocess.run([shell, "-f", "-c", line] if shell == "zsh" else [shell, "-c", line], cwd=self.top / cwd, env=self.env,
                       capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
        reached = (self.top / "ran").exists() or repo_state(self.repo) != before
        self.fresh()
        return reached
