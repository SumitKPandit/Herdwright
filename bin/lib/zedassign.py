"""Assignment rules for employees (design sections 2 and 6): who may take work, who is free, what waits. No model.

Pure functions over the task DB (a sqlite3 connection with `row_factory = sqlite3.Row`, the sessions table carrying
the employee/hat/verdict columns) and the employee files. An assignment is a session.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zedemployee as emp  # noqa: E402
import zedmem  # noqa: E402

AUTHOR_HATS = ("build", "design")  # whoever wore these on a task or branch may not review or QA it
WRITER_HATS = (None, "build")      # a session with no hat is a plain worker, which may edit source too
OPEN = ("running", "waiting_user")
def is_senior(fields):
    """The one reading of an employee's `senior` field: true, or the text "true" (front matter may give either)."""
    return fields.get("senior") is True or str(fields.get("senior")).strip().lower() == "true"


LEAD_HATS = ("design",)            # seniors rank first for these, and for work marked ambiguous
SAME_TASK_WEIGHT = 5               # a past assignment on this very task (a fixer who built it ranks high)
SAME_HAT_REPO_WEIGHT = 1
DONE_TASKS = ("done", "cancelled", "parked")


class Refused(Exception):
    """An assignment `spawn` must not start. `queued` means "not now" (the employee is busy), not "never"."""

    def __init__(self, reason, queued=False):
        super().__init__(reason)
        self.queued = queued


def _marks(values):
    return ",".join("?" * len(values))


def authors(conn, tid, repo, branch):
    """Employees who wore build or design on this task or on this branch. Every fixer joins the set."""
    rows = conn.execute(
        f"SELECT DISTINCT employee FROM sessions WHERE employee IS NOT NULL AND hat IN ({_marks(AUTHOR_HATS)}) "
        "AND (task_id=? OR (repo=? AND branch=?))", (*AUTHOR_HATS, tid, repo, branch))
    return {r["employee"] for r in rows}


def busy(conn):
    """{employee: session id} for employees with an open session."""
    rows = conn.execute(f"SELECT id, employee FROM sessions WHERE employee IS NOT NULL AND status IN ({_marks(OPEN)})", OPEN)
    return {r["employee"]: r["id"] for r in rows}


def check_assignment(conn, name, hat, tid, repo, branch, worktree=None):
    """Raise Refused unless `name` may wear `hat` on this task now. Checks, in this order: the employee and hat exist,
    the hard rule (nobody reviews or QAs work they authored), the one-writer rule on a worktree (a `build` needs the
    worktree to itself; any other hat needs it free of writers; pass worktree=None for in-place projects), and that
    the employee has no other open session (queued, not refused)."""
    emp.load_employee(name)
    if hat not in emp.hat_names():
        raise Refused(f"unknown hat {hat!r}: one of {', '.join(emp.hat_names())}")
    if hat in ("review", "qa") and name in authors(conn, tid, repo, branch):
        raise Refused(f"{name} authored T{tid} / {branch} (build or design): nobody reviews or QAs their own work. "
                      f"Pick someone else (`zed-employee suggest T{tid} --hat {hat}`).")
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
    open_id = busy(conn).get(name)
    if open_id:
        raise Refused(f"{name} is busy with S{open_id}; the task stays queued until that session ends", queued=True)


# ---------------------------------------------------------------- suggest


def familiarity(conn, name, tid, repo, hat, tags=()):
    """(score, parts): memory entries about the repo that are not stale (1 each, +1 per matching tag), past
    assignments on this task, and past assignments with this hat on this repo."""
    tags = set(tags)
    memory = 0
    path = zedmem.memory_dir(emp.employee_dir(name)) / "repos" / f"{repo}.md"
    for e in zedmem.load(path)[1]:
        if not e.get("stale"):
            memory += 1 + len(tags & set(e.tags))
    on_task = conn.execute("SELECT COUNT(*) FROM sessions WHERE employee=? AND task_id=?", (name, tid)).fetchone()[0]
    on_hat = conn.execute("SELECT COUNT(*) FROM sessions WHERE employee=? AND hat=? AND repo=?", (name, hat, repo)).fetchone()[0]
    parts = {"memory": memory, "task": on_task * SAME_TASK_WEIGHT, "hat": on_hat * SAME_HAT_REPO_WEIGHT}
    return sum(parts.values()), parts


def suggest(conn, tid, hat, repo, branch=None, tags=(), ambiguous=False):
    """Everyone hired, best pick first. Each row: name, eligible, why (when not), lead, score, parts, last.
    Eligible = free, and for review/qa not an author. Order: eligible first; among them seniors first when the hat is
    a lead hat or the work is ambiguous; then familiarity (higher first); then least recently assigned; then name.
    Familiarity only orders the free: it is never a reason to wait for someone who is busy."""
    if hat not in emp.hat_names():
        raise Refused(f"unknown hat {hat!r}: one of {', '.join(emp.hat_names())}")
    wrote = authors(conn, tid, repo, branch or "") if hat in ("review", "qa") else set()
    taken = busy(conn)
    rows = []
    for name in emp.names():
        fields, _ = emp.load_employee(name)
        score, parts = familiarity(conn, name, tid, repo, hat, tags)
        last = conn.execute("SELECT MAX(created_at) FROM sessions WHERE employee=?", (name,)).fetchone()[0] or ""
        why = (f"busy with S{taken[name]}" if name in taken else "authored this work" if name in wrote else "")
        rows.append({"name": name, "eligible": not why, "why": why, "senior": is_senior(fields),
                     "lead": is_senior(fields) and (ambiguous or hat in LEAD_HATS),
                     "score": score, "parts": parts, "last": last})
    rows.sort(key=lambda r: (not r["eligible"], not r["lead"], -r["score"], r["last"], r["name"]))
    return rows


# ---------------------------------------------------------------- board


def free_employees(conn):
    taken = busy(conn)
    return [n for n in emp.names() if n not in taken]


def _may(conn, tid, hat, repo, branch):
    return [r["name"] for r in suggest(conn, tid, hat, repo, branch) if r["eligible"]]


def board(conn):
    """{'free': [names], 'waits': [{kind, task, repo, branch, may}]}. kinds: `new` (a task nobody started), `fix`
    (a review or QA verdict of blockers after the latest build, and no build open), `review` / `qa` (the latest build has
    no verdict of that hat after it, and none is open), `ready` (both verdicts clean: ready-check is Zed's)."""
    if not emp.names():
        return {"free": [], "waits": []}
    waits = []
    for t in conn.execute("SELECT id FROM tasks WHERE status='intake' AND id NOT IN (SELECT task_id FROM sessions) ORDER BY id"):
        waits.append({"kind": "new", "task": t["id"], "repo": None, "branch": None, "may": free_employees(conn)})
    groups = {}
    for s in conn.execute("SELECT s.*, t.status AS task_status FROM sessions s JOIN tasks t ON t.id=s.task_id "
                          "WHERE s.employee IS NOT NULL ORDER BY s.id"):
        groups.setdefault((s["repo"], s["branch"]), []).append(s)
    for (repo, branch), sessions in sorted(groups.items()):
        last = sessions[-1]
        if last["task_status"] in DONE_TASKS or any(s["pr_state"] in ("merged", "closed") for s in sessions):
            continue
        tid = last["task_id"]
        builds = [s for s in sessions if s["hat"] == "build"]
        if not builds or any(s["hat"] == "build" and s["status"] in OPEN for s in sessions):
            continue
        after = [s for s in sessions if s["id"] > builds[-1]["id"]]
        verdicts = {h: [s["verdict"] for s in after if s["hat"] == h and s["verdict"]] for h in ("review", "qa")}
        opened = {h: any(s["hat"] == h and s["status"] in OPEN for s in after) for h in ("review", "qa")}

        def wait(kind, hat):
            waits.append({"kind": kind, "task": tid, "repo": repo, "branch": branch, "may": _may(conn, tid, hat, repo, branch)})

        if "blockers" in verdicts["review"] + verdicts["qa"]:
            wait("fix", "build")
            continue
        for hat in ("review", "qa"):
            if not verdicts[hat] and not opened[hat]:
                wait(hat, hat)
        if verdicts["review"] and verdicts["qa"]:
            waits.append({"kind": "ready", "task": tid, "repo": repo, "branch": branch, "may": []})
    return {"free": free_employees(conn), "waits": waits}
