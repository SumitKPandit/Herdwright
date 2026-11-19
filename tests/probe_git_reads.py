"""Manual check of the coordinator guard's git allow list (hwshell.GIT_READS) against real git. Not part of the default
test run (its name does not match `test_*.py`): it runs some 25,000 git commands and takes several minutes.

    cd tests && python3 probe_git_reads.py [--allowed-only]

Each read subcommand is tried with each of its listing words (or none) and every option `git <cmd> -h` names or the
allow list holds: alone, before a name, with a program as its next word or `=value`, and before a listing word. Every
form runs on its own scratch repo state (refs, reflogs, stash, notes, an origin, an untracked file), and the counts are
printed:
  allowed            forms the guard lets through; each must leave the repo unchanged and run no program
  allowed-and-wrote  must be 0: the guard let a write or a program through (listed)
  refused-and-wrote  writes the guard caught
  refused-harmless   reads the guard refuses although git did not write (the price of a strict allow list)
The helpers here (probe_repo, repo_state) also serve the suite's fixed real-git cases in test_hw_pretool.py.
"""
import concurrent.futures
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin" / "lib"))
import hwshell  # noqa: E402

PROGRAM = "@PROGRAM@"  # in a probe form: the path of a program that records that it ran


def probe_repo(tmp):
    """A repo at `tmp/repo` with an origin, branches, tags, a stash, a note, a reflog and an untracked file, and
    `tmp/mark`: a program that leaves `tmp/ran` behind when anything runs it."""
    tmp = Path(tmp)
    git = lambda *a, cwd=tmp: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd,
                                              capture_output=True, text=True, check=True)
    git("init", "-q", "-b", "main", "origin")
    git("commit", "-q", "--allow-empty", "-m", "root", cwd=tmp / "origin")
    git("clone", "-q", "origin", "repo")
    repo = tmp / "repo"
    git("config", "user.name", "t", cwd=repo)
    git("config", "user.email", "t@t", cwd=repo)
    (repo / "a.py").write_text("x = 1\n")
    (repo / "README.md").write_text("x\n")
    git("add", ".", cwd=repo)
    git("commit", "-qm", "x", cwd=repo)
    git("branch", "b1", cwd=repo)
    git("tag", "-am", "v1", "v1", cwd=repo)
    git("notes", "add", "-m", "n", cwd=repo)
    (repo / "a.py").write_text("x = 2\n")
    git("stash", "-q", cwd=repo)
    (repo / "u.txt").write_text("untracked\n")
    mark = tmp / "mark"
    mark.write_text(f"#!/bin/sh\ntouch '{tmp}/ran'\n")
    mark.chmod(0o755)
    old = 1_000_000_000  # file times well in the past, so a read's index refresh has nothing to record
    for p in (repo / "a.py", repo / "README.md", repo / "u.txt"):
        os.utime(p, (old, old))
    git("update-index", "--refresh", cwd=repo)
    return repo


def repo_state(repo):
    """Everything a read must leave as it was: refs, reflogs, HEAD, config, stash, notes, worktrees (the .git files but
    its objects), the index's entries and the work tree's files."""
    files = {str(p.relative_to(repo)): p.read_bytes() for p in sorted(Path(repo).rglob("*"))
             if p.is_file() and "objects" not in p.relative_to(repo).parts and p.name != "index"}
    staged = subprocess.run(["git", "-C", str(repo), "ls-files", "-s"], capture_output=True, text=True).stdout
    return files, staged


def run_git(repo, words, timeout=30):
    """Run `git <words>` in `repo` the way the probe does: no terminal, stdin closed, and every editor and pager set to
    the probe's program, so a form that starts one is seen."""
    mark = str(Path(repo).parent / "mark")
    env = {**os.environ, "GIT_EDITOR": mark, "EDITOR": mark, "VISUAL": mark, "GIT_PAGER": mark}
    words = [mark if w == PROGRAM else w.replace(PROGRAM, mark) for w in words]
    try:
        subprocess.run(["git", "-C", str(repo), *words], capture_output=True, stdin=subprocess.DEVNULL, env=env,
                       timeout=timeout)
    except subprocess.TimeoutExpired:
        pass


def help_options(sub):
    """Every option `git <sub> -h` names, `--[no-]x` as both `--x` and `--no-x`."""
    text = subprocess.run(["git", sub, "-h"], capture_output=True, text=True).stdout
    options = {f"--{p}{name}" for name in re.findall(r"--\[no-\]([\w-]+)", text) for p in ("", "no-")}
    return options | set(re.findall(r"(?<![\w\[\]-])(--?[A-Za-z0-9][\w-]*)", text))


def probe_forms(extra=()):
    """The forms to try (see the module docstring), plus `extra` (shell-quoted strings)."""
    seen = set()
    for sub, forms in hwshell.GIT_READS.items():
        words = [w for w in forms if w]
        options = help_options(sub)
        for form in forms.values():
            options |= form.flags | {f"{v}=x" for v in form.values}
        names = len(forms) > 1 or any(f.positionals is not hwshell.ANY for f in forms.values())
        tails = ([], ["x"], [PROGRAM], ["HEAD", "x"]) if names else ([], [PROGRAM])  # a pure read creates no name
        for prefix in [[]] + [[w] for w in words]:
            for tail in ([], ["x"], ["x", "y"], ["HEAD"]):
                seen.add((sub, *prefix, *tail))
            for o in sorted(options):
                for tail in tails:
                    seen.add((sub, *prefix, o, *tail))
                seen.add((sub, *prefix, f"{o.split('=')[0]}={PROGRAM}"))
        for o in sorted(options):
            for w in words:
                seen.update({(sub, o, w), (sub, o, w, "x")})
    seen.update(tuple(shlex.split(r)) for r in extra)
    return sorted(seen)


def probe(chunk):
    """[(form, allowed, changed)] for each form of `chunk`, each on a clean repo state."""
    out = []
    with tempfile.TemporaryDirectory() as root:
        repo = probe_repo(tempfile.mkdtemp(dir=root))
        for words in chunk:
            allowed = hwshell.git_only_reads(["git", *words])
            before = repo_state(repo)
            run_git(repo, list(words))
            changed = (repo.parent / "ran").exists() or repo_state(repo) != before
            out.append((words, allowed, changed))
            if changed:
                repo = probe_repo(tempfile.mkdtemp(dir=root))  # a clean state for the rest
    return out


def main():
    forms = probe_forms()
    if "--allowed-only" in sys.argv:
        forms = [f for f in forms if hwshell.git_only_reads(["git", *f])]
    workers = os.cpu_count() or 4
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        results = sum(pool.map(probe, [forms[i::workers] for i in range(workers)]), [])
    counts = {"tried": len(results), "allowed": 0, "allowed-and-wrote": 0, "refused-and-wrote": 0, "refused-harmless": 0}
    harmless, gaps = {}, []
    for words, allowed, changed in results:
        counts["allowed"] += allowed
        if allowed and changed:
            counts["allowed-and-wrote"] += 1
            gaps.append(" ".join(words))
        elif not allowed:
            counts["refused-and-wrote" if changed else "refused-harmless"] += 1
            if not changed:
                harmless.setdefault(words[0], []).append(" ".join(words))
    print(" ".join(f"{k}={v}" for k, v in counts.items()))
    for sub, items in sorted(harmless.items()):
        print(f"refused-harmless {sub}: {len(items)}, e.g. {'; '.join(items[:3])}")
    for g in gaps:
        print("GAP", g)
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
