"""Guards that turn written rules (lessons.md, playbook.md) into refusals. Pure functions: callers pass in the facts.

Each returns the reason it refuses, or None / an empty list when the action is allowed, so `bin/zed` stays thin and
every gate is unit-tested for both outcomes.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zedemployee as emp  # noqa: E402
import zedlib  # noqa: E402

GATE_HATS = ("review", "qa")
WRITER_HATS = (None, "build")      # a session with no hat is from before hats existed and may have edited source
OPEN = ("running", "waiting_user")


class Refused(Exception):
    """A session `spawn` must not start."""


def _marks(values):
    return ",".join("?" * len(values))


def check_assignment(conn, hat, tid, repo, branch, worktree=None):
    """Raise Refused unless a session may wear `hat` on this task now: the hat exists, a review or QA starts only once
    every plan step of the build is ticked (merge/push/raise-PR steps excepted), and the worktree has one writer (a
    `build` needs it to itself; any other hat needs it free of writers; pass worktree=None for in-place projects).
    Authorship has no name to check at spawn: a session is its hat's, and an author's verdict is refused when it is
    recorded (`finish`) and when it is read (`independent`)."""
    if hat not in emp.hat_names():
        raise Refused(f"unknown hat {hat!r}: one of {', '.join(emp.hat_names())}")
    if hat in GATE_HATS and (problem := zedlib.plan_gate_problem(conn, tid)):
        raise Refused(f"{hat} waits until the build is complete: {problem}")
    if worktree is not None:
        others = conn.execute(f"SELECT id, agent, hat FROM sessions WHERE worktree=? AND status IN ({_marks(OPEN)}) ORDER BY id",
                              (str(worktree), *OPEN)).fetchall()
        if hat == "build" and others:
            raise Refused(f"one writer per worktree: S{others[0]['id']} ({others[0]['agent']}) is open on {worktree}; "
                          f"a build starts only when nothing else is open on it")
        writers = [o for o in others if o["hat"] in WRITER_HATS]
        if hat != "build" and writers:
            raise Refused(f"one writer per worktree: S{writers[0]['id']} ({writers[0]['agent']}) is writing on {worktree}; "
                          f"a {hat} starts after it ends")


def independent(row):
    """True when a verdict row is independent evidence: its session wore a review or QA hat, and (when it recorded where
    it began) it left the code as it found it, which a session that wrote any of it did not."""
    return row["hat"] in GATE_HATS and not (row["core_start"] and row["verdict_tree"] != row["core_start"])


def merge_problems(conn, tree):
    """Why the code `tree` (a core_tree hash of a branch head) may not be merged into a working branch; empty when it may. It
    needs a clean review and a clean qa verdict recorded for exactly this tree (the latest verdict of each hat), each
    independent evidence (see `independent`). A commit that changes only the profile has the same tree."""
    problems = []
    for hat in GATE_HATS:
        rows = conn.execute("SELECT * FROM sessions WHERE hat=? AND verdict_tree=? ORDER BY id DESC", (hat, tree)).fetchall()
        if not rows:
            problems.append(f"no {hat} verdict recorded for this code ({tree[:10]}): the code changed after it, or none was done")
            continue
        last = rows[0]  # the latest verdict of the hat decides: a later blockers verdict (re-run, second reviewer) counts
        if not (last["verdict"] == "clean" and independent(last)):
            problems.append(f"{hat} of {tree[:10]} is not clean by a non-author (S{last['id']}, verdict {last['verdict'] or 'none'})")
    return problems


def gate_reason(worktree, sha="HEAD"):
    """Why `sha` of `worktree` may not leave it yet (`bin/zed gate check`: clean review and qa by non-authors), or None."""
    zed = Path(__file__).resolve().parent.parent / "zed"
    env = {k: v for k, v in os.environ.items() if k != "ZED_GATE_OVERRIDE"}  # only the user's own `gate check` may override
    r = subprocess.run([sys.executable, str(zed), "gate", "check", sha, str(worktree)], capture_output=True, text=True, env=env)
    return None if r.returncode == 0 else (r.stderr.strip() or r.stdout.strip() or "the merge gate did not pass")


def prompt_refusal(agent_state, redirect):
    """Prompting a `working` session derails it (playbook); only `--redirect` may, on purpose."""
    if agent_state == "working" and not redirect:
        return ("the session is working: a prompt is injected mid-task and may drop the rest of its plan. Wait for it to "
                "go idle/done, or pass --redirect if you mean to change what it is doing")
    return None


def may_auto_approve(session):
    """The watcher answers a routine permission prompt by rule only in a session Zed started."""
    return not session["external"]


def relay_prompt(words, action):
    """The prompt that relays the user's OK to post: their words verbatim plus the exact action, so that the
    auto-mode classifier sees the request (playbook: relaying an OK to post)."""
    words, action = words.strip(), action.strip()
    if not words or not action:
        raise ValueError("relay-post needs the user's words and the exact action")
    quoted = "\n".join(f"    {line}" for line in words.splitlines())
    return (f"The user said, verbatim:\n{quoted}\n"
            f"That is their OK for exactly this action, and nothing wider: {action}\n"
            "Do it now, then confirm it landed (read it back) and report what you posted.")


