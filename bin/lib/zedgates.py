"""Guards that turn written rules (lessons.md, playbook.md) into refusals. Pure functions: callers pass in the facts.

Each returns the reason it refuses, or None / an empty list when the action is allowed, so `bin/zed` stays thin and
every gate is unit-tested for both outcomes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zedassign  # noqa: E402

GATE_HATS = ("review", "qa")
CORE_SYNC_STALE_SECONDS = 3600


def merge_problems(conn, sha):
    """Why `sha` (a branch head) may not be merged into a working branch; empty when it may. It needs a clean review and
    a clean qa verdict recorded for exactly this commit (the latest verdict of each hat), each by an employee who authored none of the work."""
    problems = []
    for hat in GATE_HATS:
        rows = conn.execute("SELECT * FROM sessions WHERE hat=? AND verdict_sha=? ORDER BY id DESC", (hat, sha)).fetchall()
        if not rows:
            problems.append(f"no {hat} verdict recorded for {sha[:10]} (the branch changed after it, or none was done)")
            continue
        last = rows[0]  # the latest verdict of the hat decides: a later blockers verdict (re-run, second reviewer) counts
        if not (last["verdict"] == "clean" and last["employee"]
                and last["employee"] not in zedassign.authors(conn, last["task_id"], last["repo"], last["branch"])):
            problems.append(f"{hat} of {sha[:10]} is not clean by a non-author (S{last['id']}: {last['employee'] or 'no employee'}, "
                            f"verdict {last['verdict'] or 'none'})")
    return problems


def spawn_without_employee(free, kv):
    """`spawn` without employee= while a free employee exists needs ephemeral=<reason> (lessons: employees first)."""
    if kv.get("employee") or kv.get("ephemeral", "").strip() or not free:
        return None
    return (f"employees are free ({', '.join(free)}): assign one (`bin/zed-employee suggest <T> --hat build`, then "
            "`bin/zed assign <employee> <T> hat=...`), or pass ephemeral=\"<why no employee fits>\"")


def copilot_before_claude(runtime, claude, kv):
    """Copilot only when Claude is limited (lessons: Claude first); copilot_reason=<why> overrides."""
    if runtime != "copilot" or kv.get("copilot_reason", "").strip():
        return None
    if claude.get("installed") and claude.get("enabled") and not claude.get("limited"):
        return ("claude is usable (not marked limited), and Copilot is only for when it is limited: run it on claude "
                "(it queues beyond max_running), or pass copilot_reason=\"<why>\"")
    return None


def prompt_refusal(agent_state, redirect):
    """Prompting a `working` session derails it (playbook); only `--redirect` may, on purpose."""
    if agent_state == "working" and not redirect:
        return ("the session is working: a prompt is injected mid-task and may drop the rest of its plan. Wait for it to "
                "go idle/done, or pass --redirect if you mean to change what it is doing")
    return None


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


def core_sync_stale(oldest_unsynced_ts, now_ts, limit=CORE_SYNC_STALE_SECONDS):
    """True when the oldest core commit still waiting for `main` is older than `limit` seconds."""
    return oldest_unsynced_ts is not None and now_ts - oldest_unsynced_ts > limit
