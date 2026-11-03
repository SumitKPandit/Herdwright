"""Shared helpers for the bin/zed-<name> scripts (ported from firstmate's fm-timeout-lib, fm-line-cap-lib).

Each script puts this folder on sys.path itself and imports what it needs.
Nothing here touches the task DB for writing; the scripts are read-only unless they say otherwise.
"""
import hashlib
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path

ZED_HOME = Path(__file__).resolve().parent.parent.parent
PROFILE_DIR = Path(os.environ.get("ZED_PROFILE_DIR") or ZED_HOME / "profile").expanduser().resolve()
DB_PATH = PROFILE_DIR / "db" / "zed.db"
PROJECTS_PATH = PROFILE_DIR / "config" / "projects.json"
BRIEFS_DIR = PROFILE_DIR / "briefs"

# Hard stop for any command a script runs, so a hung gh/herdr/git never hangs a turn.
DEFAULT_TIMEOUT = 30


class Result:
    def __init__(self, returncode, stdout="", stderr="", timed_out=False):
        self.returncode, self.stdout, self.stderr, self.timed_out = returncode, stdout, stderr, timed_out

    @property
    def ok(self):
        return self.returncode == 0 and not self.timed_out


def run_bounded(cmd, timeout=DEFAULT_TIMEOUT, cwd=None, env=None):
    """Run cmd with a hard time limit; never raises. A missing binary is returncode 127, a timeout is timed_out."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env, timeout=timeout)
        return Result(r.returncode, r.stdout, r.stderr)
    except subprocess.TimeoutExpired as e:
        return Result(124, (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""),
                      (e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or ""), timed_out=True)
    except FileNotFoundError:
        return Result(127, "", f"{cmd[0]}: command not found")


def git(path, *args, timeout=DEFAULT_TIMEOUT):
    return run_bounded(["git", "-C", str(path), *args], timeout=timeout)


def cap_lines(lines, limit=40, width=200):
    """At most `limit` lines of at most `width` chars each, then '... N more omitted'."""
    lines = list(lines)
    out = [ln if len(ln) <= width else ln[:width - 1] + "…" for ln in lines[:limit]]
    if len(lines) > limit:
        out.append(f"... {len(lines) - limit} more omitted")
    return out


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return default


def projects():
    return (read_json(PROJECTS_PATH, {}) or {}).get("repos", {})


def settings():
    return read_json(PROFILE_DIR / "config" / "settings.json", {}) or {}


def expand(path):
    return Path(os.path.expanduser(str(path)))


SCRATCH_NAME = re.compile(r"T\d+-[a-z][a-z0-9_-]*")


def scratch_dirs(project_path):
    """Scratch folders (T<id>-<name>) directly inside an in-place (non-git) project folder."""
    p = Path(project_path)
    return sorted(d for d in p.iterdir() if d.is_dir() and SCRATCH_NAME.fullmatch(d.name)) if p.is_dir() else []


def scratch_unique_work(scratch):
    """Reasons a scratch folder holds work that exists nowhere else: nested git repos with tracked changes or
    commits no remote has. Everything else in it is scratch by definition."""
    reasons = []
    for root, dirs, _ in os.walk(scratch):
        dirs[:] = [d for d in dirs if d != "node_modules"]
        if not os.path.lexists(os.path.join(root, ".git")):
            continue
        dirs[:] = []  # a repo: don't look inside it
        rel = os.path.relpath(root, scratch)
        if git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
            reasons.append(f"{rel}: uncommitted changes")
        elif git(root, "log", "--oneline", "--branches", "--not", "--remotes").stdout.strip():
            reasons.append(f"{rel}: commits not on any remote")
    return reasons


def db_readonly():
    """Read-only connection to the task DB; None when there is no DB yet."""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def live_agents():
    """herdr agents by pane id; {} when herdr is unavailable."""
    r = run_bounded(["herdr", "agent", "list"], timeout=10)
    if not r.ok:
        return {}
    try:
        return {a["pane_id"]: a for a in json.loads(r.stdout)["result"]["agents"]}
    except (ValueError, KeyError):
        return {}


# a session wears a hat and may carry a verdict: `verdict_tree` is the core-tree hash it is about (core_tree), `core_start`
# the same hash when the session began. Databases from older versions keep their unused columns and tables.
SESSION_COLUMNS = [("hat", "TEXT"), ("verdict", "TEXT"), ("verdict_tree", "TEXT"), ("plan", "TEXT"), ("core_start", "TEXT")]


def ensure_session_schema(conn):
    """Add the columns hat sessions need to the sessions table. Idempotent."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    for col, decl in SESSION_COLUMNS if cols else []:  # no sessions table yet: bin/zed creates it, then calls us again
        if col not in cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {decl}")


PROFILE_ENTRY = "profile"  # the top-level directory holding a profile inside a Zed checkout
CORE_MARKER = "profile.template"  # present in a Zed checkout only


def core_tree(worktree, rev="HEAD"):
    """A hash of what `rev` holds apart from the profile: a verdict is about this, not about the commit, so a commit
    that touches only `profile/` keeps it and any other change replaces it. In a Zed checkout (it has
    profile.template/) the tree's `profile` entry is left out; in any other repo the hash is its tree id.
    None when the worktree is not a git checkout."""
    listing = git(worktree, "ls-tree", rev)
    if not listing.ok:
        return None
    lines = listing.stdout.splitlines()
    names = {ln.split("\t", 1)[1] for ln in lines if "\t" in ln}
    if CORE_MARKER not in names:
        tree = git(worktree, "rev-parse", f"{rev}^{{tree}}")
        return tree.stdout.strip() if tree.ok else None
    kept = [ln for ln in lines if ln.split("\t", 1)[-1] != PROFILE_ENTRY]
    return hashlib.sha256("\n".join(kept).encode()).hexdigest()


SECRET_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Atlassian token", re.compile(r"\bATATT[A-Za-z0-9_=-]{10,}")),
    ("API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I)),
    ("credential assignment", re.compile(r"\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)\b"
                                         r"[\"']?\s*[:=]\s*[\"']?[^\s\"',;]{6,}", re.I)),
]


def find_secret(text):
    """Name of the first secret-looking pattern in text, else None. Credentials are never stored or sent."""
    for name, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return name
    return None


def emit(data, as_json, render):
    """Print data as JSON, or render(data) -> lines as capped text."""
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print("\n".join(render(data)))


def parse_plan_steps(text):
    """The plan file's checklist: [{'n': int, 'done': bool, 'text': str, 'line': str}], in order."""
    steps = []
    for m in re.finditer(r"^\s*- \[([ xX])\]\s*(.*)$", text, re.M):
        body = m.group(2)
        n = re.match(r"(\d+)\.", body)
        steps.append({"n": int(n.group(1)) if n else len(steps) + 1, "done": m.group(1) != " ",
                      "text": body, "line": m.group(0)})
    return steps


# A step is exempt from the completion gate when what it does is merge, push or raise a PR: those happen after review
# and QA (user, 2026-10-27). Say so explicitly with the marker `[after-review]` in the step's title, or start the title
# with the action itself ("Merge the branch", "Push to origin", "Raise a draft PR against main"). A bare word anywhere
# in the title ("Push notification handler", "Implement merge logic") never exempts a step.
AFTER_REVIEW_MARKER = "[after-review]"
EXEMPT_STEP = re.compile(
    r"^(?:(?:merge|push)\s+(?:the\s+|this\s+|my\s+)?(?:branch|main|master|commits?|changes|work|prs?|pull\s+requests?|to\s+(?:origin|remote|main))"
    r"|push\s+`[^`]*`"
    r"|push\s+and\s+(?:raise|open|create)\s+(?:an?\s+|the\s+)?(?:draft\s+)?(?:prs?|pull\s+requests?)"
    r"|(?:rais(?:e|es)|open|creat(?:e|es)|fil(?:e|es)|submit)\s+(?:an?\s+|the\s+|my\s+)?(?:draft\s+|ready\s+)?(?:prs?|pull\s+requests?))"
    r"(?=\s*$|\s*[.:;,(\u2014\u2013-]|\s+(?:against|to|for|on|with|from|once|after|then|vs|and)\b)", re.I)
# A tick that covers several steps at once: "1-5 done", "steps 2 to 4", "all done".
CATCH_ALL_STEP = re.compile(r"^(?:\d+\s*(?:-|\u2013|\u2014|to|\.\.)\s*\d+\b|steps?\s+\d+\s*(?:-|\u2013|\u2014|to|,|and|&)\s*\d+\b|all\b[^.]*\bdone\b)", re.I)
# The step a worker can only tick by finishing: `zed-employee finish` ticks it itself. It is exactly the line the brief
# template writes as the plan's last step, `- [ ] Run zed-employee finish and report (ZED:DONE)` (backticks, a leading
# "N." and a trailing "done when ..." or "~time" are ignored); any other wording is an ordinary step.
FINISH_STEP_TITLE = "run zed-employee finish and report (zed:done)"


def step_title(step):
    return re.split(r"done when", step["text"], maxsplit=1, flags=re.I)[0]


def step_label(step):
    return re.sub(r"^\d+\.\s*", "", step_title(step)).strip(" .:")[:60]


def step_exempt(step):
    title = re.sub(r"^\d+\.\s*", "", step_title(step)).strip(" .:")  # the whole title: step_label cuts it for display
    return AFTER_REVIEW_MARKER in title.lower() or bool(EXEMPT_STEP.match(title.lstrip("*_` ")))


def step_catch_all(step):
    return bool(CATCH_ALL_STEP.match(re.sub(r"^\d+\.\s*", "", step["text"])))


def plan_open(steps):
    """(blocking, exempt): the unticked steps that must be done before review/QA, and the unticked merge/push/raise-PR
    steps that wait until after. A catch-all tick never counts as done."""
    open_ = [s for s in steps if not s["done"] or step_catch_all(s)]
    return [s for s in open_ if not step_exempt(s)], [s for s in open_ if step_exempt(s)]


def brief_plan_file(brief_text):
    """The plan file a brief tells its worker to write (the first `...-plan.md` in its '## Plan' section), or None."""
    section = re.search(r"^## Plan\b(.*?)(?=^## |\Z)", brief_text, re.M | re.S)
    m = re.search(r"`([^`\s]+-plan\.md)`", section.group(1)) if section else None
    return Path(m.group(1)) if m else None


AUTHOR_SESSION = "(hat IS NULL OR hat = 'build')"  # a session that implements: the build hat and sessions from before hats existed


def session_plan(s):
    """The session's own plan file (a Path), or None: recorded at spawn from its brief. A row from a DB older than
    sessions.plan has none."""
    return Path(s["plan"]) if "plan" in s.keys() and s["plan"] else None


def read_steps(path):
    try:
        return parse_plan_steps(Path(path).read_text()) if path else []
    except OSError:
        return []


def task_plans(conn, task_id):
    """The plan files that make up a task's progress: those of its build and fix sessions (never review, QA or verify
    plans), oldest first, each once."""
    seen = []
    for r in conn.execute(f"SELECT plan FROM sessions WHERE task_id=? AND external=0 AND {AUTHOR_SESSION} AND plan!='' ORDER BY id", (task_id,)):
        if Path(r[0]) not in seen and Path(r[0]).is_file():
            seen.append(Path(r[0]))
    return seen


def gate_plan(conn, task_id):
    """The plan the completion gate reads: that of the task's latest build or fix session. It was written for the
    whole task (finished steps start ticked), so older plans cannot hold it back."""
    for r in conn.execute(f"SELECT plan FROM sessions WHERE task_id=? AND external=0 AND {AUTHOR_SESSION} AND plan!='' ORDER BY id DESC", (task_id,)):
        if Path(r[0]).is_file():
            return Path(r[0])
    return None


def is_finish_step(step):
    title = re.sub(r"^\d+\.\s*", "", step_title(step)).replace("`", "")
    return re.sub(r"(?:~.*|[\s.:;\u2014\u2013-])+$", "", title).strip().lower() == FINISH_STEP_TITLE


def finish_step(steps):
    """The last step when it is the unticked `Run zed-employee finish and report (ZED:DONE)` line, else None."""
    if steps and not steps[-1]["done"] and is_finish_step(steps[-1]):
        return steps[-1]


def open_steps_problem(steps, what, skip=()):
    """Why a plan's steps are not complete enough to hand on, or None. `skip` are steps the caller settles itself."""
    blocking = [s for s in plan_open(steps)[0] if s not in skip]
    if not blocking:
        return None
    shown = "; ".join(f"step {s['n']}" + (" (catch-all tick)" if s["done"] else "") + ": " + step_label(s) for s in blocking[:4])
    more = f" (+{len(blocking) - 4} more)" if len(blocking) > 4 else ""
    return (f"{what} has {len(blocking)} step(s) not ticked individually: {shown}{more}. "
            f"Tick each as it is done; only merge/push/raise-PR steps ({AFTER_REVIEW_MARKER} or starting with that action) may stay open")


def plan_gate_problem(conn, task_id):
    """Why the task's build is not complete enough for review/QA, or None. A task with no recorded build plan is not
    gated here (`spawn` demands a plan, `zed-plan lint` checks it); only sessions older than sessions.plan lack one."""
    path = gate_plan(conn, task_id)
    return open_steps_problem(read_steps(path), f"the build plan of T{task_id} ({path.name})") if path else None


def session_plan_problem(s, skip_finish=False):
    """Why the session's own plan is not complete, or None; with skip_finish the 'run finish / report' step is left to finish."""
    path = session_plan(s)
    steps = read_steps(path)
    last = finish_step(steps) if skip_finish else None
    return open_steps_problem(steps, f"S{s['id']}'s plan ({path.name})" if path else "", [last] if last else [])


def tick_step(path, step):
    """Tick one step of a plan file in place."""
    text = Path(path).read_text()
    Path(path).write_text(text.replace(step["line"], step["line"].replace("[ ]", "[x]", 1), 1))

