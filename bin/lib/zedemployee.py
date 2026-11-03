"""Hats: manifests, skills, charters and `finish`, the end of a hat session. No model.

Core ships the hat templates in templates/hats/ and the skills every hat shares in templates/skills/. A session wears one hat (build, review or qa) for one task;
it has no name of its own and keeps nothing between sessions: the DB, the plan and the findings drafts are its record.
"""
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zedlib  # noqa: E402

HATS = zedlib.ZED_HOME / "templates" / "hats"
SHARED_SKILLS = zedlib.ZED_HOME / "templates" / "skills"
SUBAGENT_MODES = ["none", "read-only", "free"]
MANIFEST_LISTS = ["scripts", "skills"]
HAT_CHARTER_WORDS = 150


class HatError(Exception):
    pass


# ---------------------------------------------------------------- hats and skills


def hat_names():
    return sorted(p.name for p in HATS.iterdir() if p.is_dir()) if HATS.is_dir() else []


def validate_manifest(manifest, hat_dir=None):
    """Problems with a hat's capabilities.json (empty list = valid). With hat_dir, also that the charter exists and
    stays under 150 words and that every listed skill resolves to a SKILL.md."""
    problems = []
    if not isinstance(manifest, dict):
        return ["manifest is not a JSON object"]
    for key in MANIFEST_LISTS:
        v = manifest.get(key)
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            problems.append(f"{key}: must be a list of names")
    deny = manifest.get("deny")
    if not isinstance(deny, dict) or not {"edit", "commands"} <= set(deny) <= {"edit", "commands", "except", "gated"} \
            or not all(isinstance(deny[k], list) and all(isinstance(x, str) for x in deny[k]) for k in deny):
        problems.append("deny: must be {\"edit\": [globs], \"commands\": [prefixes]} and may add \"except\": [globs] and \"gated\": [prefixes]")
    if manifest.get("subagents") not in SUBAGENT_MODES:
        problems.append(f"subagents: must be one of {SUBAGENT_MODES}")
    if not isinstance(manifest.get("kind"), str) or not manifest.get("kind"):
        problems.append("kind: must be a non-empty string")
    extra = set(manifest) - {"scripts", "skills", "deny", "subagents", "kind"}
    if extra:
        problems.append(f"unknown keys: {sorted(extra)}")
    if hat_dir and not problems:
        charter = Path(hat_dir) / "hat.md"
        if not charter.exists():
            problems.append("hat.md missing")
        elif len(charter.read_text().split()) > HAT_CHARTER_WORDS:
            problems.append(f"hat.md is over {HAT_CHARTER_WORDS} words")
        for skill in manifest["skills"]:
            if skill_path(skill, Path(hat_dir).name) is None:
                problems.append(f"skill {skill!r} has no SKILL.md")
    return problems


def load_manifest(hat):
    path = HATS / hat / "capabilities.json"
    if not path.exists():
        raise HatError(f"unknown hat {hat!r}: one of {', '.join(hat_names())}")
    return json.loads(path.read_text())


def skill_path(name, hat=None):
    """The SKILL.md for a skill: the hat's (any hat when none is given), then the ones every hat shares."""
    candidates = []
    for h in [hat] if hat else hat_names():
        candidates.append(HATS / h / "skills" / name / "SKILL.md")
    candidates.append(SHARED_SKILLS / name / "SKILL.md")
    return next((p for p in candidates if p.exists()), None)


def skill_names(hat=None):
    names = {p.parent.name for p in SHARED_SKILLS.glob("*/SKILL.md")}
    for h in [hat] if hat else hat_names():
        names |= {p.parent.name for p in (HATS / h / "skills").glob("*/SKILL.md")}
    return sorted(names)


# ---------------------------------------------------------------- the task DB


def connect():
    DB = zedlib.DB_PATH
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    zedlib.ensure_session_schema(conn)
    return conn


@contextlib.contextmanager
def database():
    """The task DB for one unit of work: committed on success, rolled back on an error, always closed."""
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def hat_charter(hat):
    """The hat's charter, appended to the worker rules of a session wearing it."""
    return (HATS / hat / "hat.md").read_text().strip() + "\n"


# ---------------------------------------------------------------- finish

VERDICTS = ["clean", "blockers"]
VERDICT_HATS = ("review", "qa")
NOGIT_PREFIX = "nogit:"


def in_place_project(repo):
    """True for a project that runs in place without a git worktree (`worktrees: false` in projects.json)."""
    return (zedlib.projects().get(repo) or {}).get("worktrees") is False


def nogit_stand_in(sid, draft):
    """The stand-in for a head sha in a folder without git: a hash of the findings draft, else of the session id. It never
    equals a commit sha, so the git-only merge gate can never match it."""
    data = Path(draft).read_bytes() if draft else f"S{sid}".encode()
    return NOGIT_PREFIX + hashlib.sha256(data).hexdigest()


class FinishRefused(Exception):
    """`finish` refused: the message says what to do before running it again."""


def stop_problem(session=None):
    """Why a worker's turn may not end yet: it has reported `done:` in its status file but has not run `finish`
    (the status file is the one report workers write before ZED:DONE). None when it may stop.
    Fails open when the session or its files cannot be read: a hook must never wedge a turn."""
    sid = str(session or "").lstrip("S")
    if not sid.isdigit():
        return None
    log = zedlib.PROFILE_DIR / ".cache" / "status" / f"S{sid}.log"
    if not log.is_file() or not re.search(r"^done:", log.read_text(errors="replace"), re.M):
        return None
    with database() as conn:
        if conn.execute("SELECT 1 FROM events WHERE kind='finish' AND text LIKE ?", (f"S{sid} %",)).fetchone():
            return None
    return (f"S{sid} reported done but `zed-employee finish` has not run: run it (it ticks the plan's last step "
            "and records a verdict), then print your ZED:DONE block")


def place_draft(draft, tid, hat, sid):
    """Copy a findings draft to profile/briefs/T<task>-<hat>-S<session>-findings.md (where a review/qa hat may write
    it, and where whoever fixes looks) unless it is already there; returns the path the record should name."""
    dest = zedlib.PROFILE_DIR / "briefs" / f"T{tid}-{hat}-S{sid}-findings.md"
    if Path(draft).resolve() != dest.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(draft, dest)
    return str(dest)


def plan_progress(session):
    """(done, total) steps of the session's own plan."""
    steps = zedlib.read_steps(zedlib.session_plan(session))
    return sum(s["done"] for s in steps), len(steps)


def session_number(text):
    """`S12` or `12` -> 12; anything else is refused with a message."""
    m = re.fullmatch(r"S?(\d+)", str(text or ""))
    if not m:
        raise FinishRefused(f"{text!r} is not a session id (S<number>)")
    return int(m.group(1))


def ancestors(pid=None):
    """The pids from `pid` (default: this process) up to the root, read from the kernel's parent links, which a process cannot rewrite."""
    chain, pid = [], os.getpid() if pid is None else pid
    while pid > 1 and pid not in chain:
        chain.append(pid)
        r = zedlib.run_bounded(["ps", "-o", "ppid=", "-p", str(pid)], timeout=10)
        pid = int(r.stdout.strip()) if r.ok and r.stdout.strip().isdigit() else 0
    return chain


def process_pane(hint=None):
    """The herdr pane that hosts this process (its shell or a foreground process is an ancestor of it), or None when no
    pane does or herdr cannot say. Unlike $HERDR_PANE_ID, which any caller can set, this comes from the process tree;
    `hint` (the claimed pane) is only tried first. A script a worker detaches from its pane is in no pane."""
    def herdr(*argv):
        r = zedlib.run_bounded(["herdr", "pane", *argv], timeout=10)
        try:
            return json.loads(r.stdout)["result"] if r.ok else {}
        except (ValueError, KeyError, TypeError):
            return {}
    chain = set(ancestors())
    ids = [p.get("pane_id") for p in herdr("list").get("panes", [])]
    for pane in sorted(filter(None, ids), key=lambda i: i != hint):
        info = herdr("process-info", "--pane", pane).get("process_info", {})
        pids = {info.get("shell_pid"), *(p.get("pid") for p in info.get("foreground_processes", []))}
        if chain & pids:
            return pane
    return None


def caller_session(conn, env=None, pane=None):
    """The hat session running this shell, or None in a shell that is no hat session (Zed's). The identity is the session of
    the herdr pane that hosts this process (`process_pane`): $HERDR_PANE_ID, $ZED_SESSION and $ZED_HAT are only claims, and a
    claim that disagrees with the pane, or a shell in no known pane that makes one, is refused. `zed-deny` also keeps a hat
    session from setting or unsetting them; a worker's own detached script is out of scope (README)."""
    env = os.environ if env is None else env
    claims = [k for k in ("ZED_SESSION", "ZED_HAT", "HERDR_PANE_ID") if env.get(k)]
    pane = process_pane(env.get("HERDR_PANE_ID")) if pane is None else pane
    if env.get("HERDR_PANE_ID") and env["HERDR_PANE_ID"] != pane:
        raise FinishRefused(f"HERDR_PANE_ID says {env['HERDR_PANE_ID']} but this process runs in "
                            f"{pane or 'no herdr pane'}: the identity is taken from the process, not the variable")
    row = conn.execute("SELECT id, hat FROM sessions WHERE pane_id=? ORDER BY id DESC LIMIT 1", (pane,)).fetchone() if pane else None
    if not row:
        if claims and (env.get("ZED_HAT") or env.get("ZED_SESSION")):
            raise FinishRefused("this is a hat session whose own session cannot be read (its process is in no pane of a session)")
        return None
    if env.get("ZED_SESSION") and session_number(env["ZED_SESSION"]) != row["id"]:
        raise FinishRefused(f"this process runs in the pane of S{row['id']} but ZED_SESSION says {env['ZED_SESSION']}")
    if env.get("ZED_HAT") and env["ZED_HAT"] != row["hat"]:
        raise FinishRefused(f"this process runs in the pane of S{row['id']} ({row['hat']}) but ZED_HAT says {env['ZED_HAT']}")
    return row["id"]


def herdr_session(pane):
    """The agent session herdr itself records for `pane`, or None (no herdr, unknown pane, no session)."""
    r = zedlib.run_bounded(["herdr", "pane", "get", pane], timeout=10)
    try:
        return (json.loads(r.stdout)["result"]["pane"].get("agent_session") or {}).get("value") if r.ok else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def caller_is_zed(pane):
    """True only when this process is POSITIVELY Zed: it runs in the pane the profile lock names, herdr says that pane
    runs the lock's session, and the lock's own pid (Zed's Claude or OpenCode process) is one of its ancestors. Naming
    Zed's pane (readable in the lock) is not enough; a missing or unreadable identity is never Zed."""
    lock = zedlib.read_json(zedlib.PROFILE_DIR / ".cache" / "zed.lock")
    if not (pane and isinstance(lock, dict) and lock.get("agent_session") and lock.get("pane") == pane):
        return False
    return lock.get("pid") in ancestors() and herdr_session(pane) == lock["agent_session"]


def target_session(conn, session=None, as_zed=False, env=None):
    """The session id `finish` acts on. A hat session finishes only itself; `--as-zed --session S<n>` finishes another
    one (a stuck worker's) and is accepted only from Zed's own process (`caller_is_zed`), never for want of an identity."""
    env = os.environ if env is None else env
    pane = process_pane(env.get("HERDR_PANE_ID"))
    caller = caller_session(conn, env, pane)
    if as_zed:
        if caller or env.get("ZED_HAT") or not caller_is_zed(pane):
            raise FinishRefused("--as-zed is for Zed's own process (in the pane of the session holding the profile lock): "
                                "a hat session finishes only itself")
        if not session:
            raise FinishRefused("--as-zed needs --session S<id>")
        return session_number(session)
    if session and session_number(session) != caller:
        raise FinishRefused(f"S{session_number(session)} is not this session ({f'S{caller}' if caller else 'no hat session'}): "
                            "a session finishes only itself")
    if caller is None:
        raise FinishRefused("this shell is not a hat session: Zed finishes a stuck worker with --as-zed --session S<id>")
    return caller


def finish(session, *, verdict=None, draft=None, today=None):
    """End a hat session, no model. Refuses (FinishRefused) before changing anything unless: a review/qa has a
    verdict (and a findings draft when it is `blockers`), a build has no verdict, the session's own plan has every
    step ticked but the merge/push/raise-PR ones and the final "finish / report" step (which `finish` then ticks
    itself), a review/qa left the code as it found it (one that changed it is an author and gives no verdict), and
    the session was not finished before. Then the verdict is stored with the core-tree hash it is about, and the
    findings draft is recorded for whoever fixes. Who may finish which session is `target_session`'s rule.
    Returns the report lines."""
    today = today or date.today().isoformat()
    sid = session_number(session)
    with database() as conn:
        s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        if not s or not s["hat"]:
            raise FinishRefused(f"S{sid} is not a hat session")
        tid, hat, repo = s["task_id"], s["hat"], s["repo"]
        if conn.execute("SELECT 1 FROM events WHERE kind='finish' AND text LIKE ?", (f"S{sid} %",)).fetchone():
            raise FinishRefused(f"S{sid} is already finished")
        if hat in VERDICT_HATS:
            if verdict not in VERDICTS:
                raise FinishRefused(f"a {hat} session needs --verdict {'|'.join(VERDICTS)}")
        elif verdict:
            raise FinishRefused(f"--verdict is for {' and '.join(VERDICT_HATS)} sessions; this one wore {hat}")
        if problem := zedlib.session_plan_problem(s, skip_finish=True):  # finish ticks its own "run finish / report" step
            raise FinishRefused(problem)
        if draft:
            draft = str(Path(draft).expanduser().resolve())
            if not Path(draft).is_file():
                raise FinishRefused(f"findings draft {draft} does not exist")
        elif verdict == "blockers":
            raise FinishRefused("a verdict of blockers needs --draft <findings file>: whoever fixes starts from it")
        tree = zedlib.core_tree(s["worktree"]) if verdict else None
        if verdict and not tree:
            if not in_place_project(repo):
                raise FinishRefused(f"cannot read the commit of {s['worktree']}: a verdict is recorded with the code it is about")
            tree = nogit_stand_in(sid, draft)
        elif verdict and s["core_start"] and tree != s["core_start"]:
            raise FinishRefused(f"the code of {s['worktree']} changed during this {hat} session: whoever changes it is its author "
                                "and cannot give the verdict. Ask Zed for a fresh session")
        if step := zedlib.finish_step(zedlib.read_steps(zedlib.session_plan(s))):
            zedlib.tick_step(zedlib.session_plan(s), step)
        done, total = plan_progress(s)
        if verdict:
            conn.execute("UPDATE sessions SET verdict=?, verdict_tree=? WHERE id=?", (verdict, tree, sid))
        if draft:
            draft = place_draft(draft, tid, hat, sid)
            conn.execute("INSERT INTO events(task_id, ts, kind, text) VALUES (?,?,?,?)",
                         (tid, today, "findings", f"S{sid} {hat} {verdict or 'notes'}: {draft}"))
        conn.execute("INSERT INTO events(task_id, ts, kind, text) VALUES (?,?,?,?)",
                     (tid, today, "finish", f"S{sid} {hat}" + (f" {verdict}" if verdict else "")))
    lines = [f"finished S{sid} ({hat}, T{tid})",
             f"  plan {done}/{total} steps ticked" + ("" if done == total else " (only merge/push/raise-PR steps open)")]
    if verdict:
        lines.append(f"  verdict {verdict}" + (f", findings {draft}" if draft else ""))
    return lines
