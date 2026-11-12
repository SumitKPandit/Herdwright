"""The lifecycle conductor: facts in, actions out.

The decisions are functions of the DB, so a crashed watcher loses nothing and a re-run does nothing twice. The only
effects are `bin/hw` commands run through `cli`, the same entry points a person uses, so every gate applies unchanged.
A finished hat session is closed once it has run `hw-employee finish` (its herdr pane and spend are settled).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

FINISH_WAIT = 60  # seconds a DONE session may wait for its `hw-employee finish` before Herdwright is told


def _run(conn, cli, args):
    """Run a `bin/hw` child. It writes the same DB, so the parent must not hold a write transaction meanwhile."""
    conn.commit()
    return cli(args)


def _event(conn, tid, text):
    conn.execute("INSERT INTO events(task_id, ts, kind, text) VALUES (?,datetime('now','localtime'),'conductor',?)", (tid, text))


def finished_sessions(conn):
    """Hat sessions that reported DONE and are not closed yet, split by whether `hw-employee finish` has run.
    A finish event also means the plan was complete: `finish` refuses otherwise."""
    done, waiting = [], []
    for s in conn.execute("SELECT * FROM sessions WHERE status='finished' AND external=0 AND hat IS NOT NULL ORDER BY id"):
        ran = conn.execute("SELECT 1 FROM events WHERE kind='finish' AND text LIKE ?", (f"S{s['id']} %",)).fetchone()
        (done if ran else waiting).append(s)
    return done, waiting


def tick(conn, alerts, cli, clock=time.time):
    """One conductor pass for the watcher. Closes finished sessions that ran `finish` (keeping their worktree: review and
    QA read it) and flags one that has not after FINISH_WAIT. Returns the lines that need Herdwright."""
    mine = alerts.setdefault("conductor", {})
    waiting_since = mine.setdefault("finishing", {})
    wakes = []
    done, waiting = finished_sessions(conn)
    for s in done:
        waiting_since.pop(str(s["id"]), None)
        rc, out = _run(conn, cli, ["close", f"S{s['id']}", "keep-worktree"])
        if rc == 0:
            _event(conn, s["task_id"], f"S{s['id']} {s['hat']} finished: closed")
        elif mine.setdefault("close_failed", {}).get(str(s["id"])) != out:
            mine["close_failed"][str(s["id"])] = out
            wakes.append(f"S{s['id']} T{s['task_id']} finished but close failed: {out.strip().splitlines()[-1] if out.strip() else rc}")
    live = {str(s["id"]) for s in waiting}
    for sid in [k for k in waiting_since if k not in live]:
        del waiting_since[sid]
    for s in waiting:
        first = waiting_since.setdefault(str(s["id"]), clock())
        if clock() - first >= FINISH_WAIT and not mine.setdefault("flagged", {}).get(str(s["id"])):
            mine["flagged"][str(s["id"])] = True
            wakes.append(f"S{s['id']} T{s['task_id']} {s['hat']} reported DONE {FINISH_WAIT} s ago and has not run "
                         "`hw-employee finish`: it is not closed; send it back to finish")
    conn.commit()
    return wakes
