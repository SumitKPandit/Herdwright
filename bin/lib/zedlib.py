"""Shared helpers for the bin/zed-<name> scripts (ported from firstmate's fm-timeout-lib, fm-line-cap-lib).

Each script puts this folder on sys.path itself and imports what it needs.
Nothing here touches the task DB for writing; the scripts are read-only unless they say otherwise.
"""
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


EMPLOYEE_SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
  name TEXT PRIMARY KEY,
  senior INTEGER NOT NULL DEFAULT 0,
  hired_at TEXT NOT NULL,
  introduced_at TEXT
);
"""
# an assignment is a session: who wore which hat, how it ended, and who asked for it
EMPLOYEE_SESSION_COLUMNS = [("employee", "TEXT"), ("hat", "TEXT"), ("verdict", "TEXT"), ("verdict_sha", "TEXT"), ("origin", "TEXT"),
                            ("direct", "INTEGER NOT NULL DEFAULT 0")]


def ensure_employee_schema(conn):
    """Create the employees table and add the sessions columns assignments need. Idempotent, no behaviour change."""
    conn.executescript(EMPLOYEE_SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    for col, decl in EMPLOYEE_SESSION_COLUMNS if cols else []:  # no sessions table yet: bin/zed creates it, then calls us again
        if col not in cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {decl}")


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

