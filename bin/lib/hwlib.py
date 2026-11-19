"""Shared helpers for the bin/hw-<name> scripts. Ported from firstmate's fm-timeout-lib / fm-line-cap-lib.
firstmate: Copyright (c) 2026 Kun Chen, MIT licence, see THIRD_PARTY_LICENSES.md.

Each script puts this folder on sys.path itself and imports what it needs.
Nothing here touches the task DB for writing; the scripts are read-only unless they say otherwise.
"""
import hashlib
import json
import os
import re
import sqlite3
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HW_HOME = Path(__file__).resolve().parent.parent.parent
PROFILE_DIR = Path(os.environ.get("HW_PROFILE_DIR") or HW_HOME / "profile").expanduser().resolve()
DB_PATH = PROFILE_DIR / "db" / "hw.db"
OLD_DB_PATH = PROFILE_DIR / "db" / "zed.db"  # its name before the rename to Herdwright; `bin/hw start` renames it once
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


DEFAULT_ASSISTANT_NAME = "Herdwright"


def assistant_name():
    """What the user calls the assistant: `assistant_name` in the profile's settings (bin/hw-setup asks for it)."""
    return settings().get("assistant_name") or DEFAULT_ASSISTANT_NAME


def expand(path):
    return Path(os.path.expanduser(str(path)))


SCRATCH_NAME = re.compile(r"T\d+-[a-z][a-z0-9_-]*")


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


def refuse_old_database(program):
    """Stop a command that would create an empty database in front of the one a profile from before the rename
    still has under its old name: the tasks, sessions and questions in it would look gone."""
    if OLD_DB_PATH.exists() and not DB_PATH.exists():
        sys.exit(f"{program}: this profile is from before the rename to Herdwright: its database is still {OLD_DB_PATH}. "
                 "Run `bin/hw start` first: it renames the profile's old names once.")


def db_readonly():
    """Read-only connection to the task DB; None when there is no DB yet. A profile from before the rename that
    `bin/hw start` has not renamed yet reads as "no DB" here: the read-only helpers then report nothing, while every
    command that would create a database refuses (refuse_old_database)."""
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
    """Add the columns hat sessions need to the sessions table, a task's `size` to the tasks table (TASK_SIZES), and the one answers need to the questions table
    (`delivered_at`: when the session that asked was given the answer; empty while it has not read it). Idempotent."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    for col, decl in SESSION_COLUMNS if cols else []:  # no sessions table yet: bin/hw creates it, then calls us again
        if col not in cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {decl}")
    tasks = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    if tasks and "size" not in tasks:  # a task from before the four steps has none: it keeps the flow it started in
        conn.execute("ALTER TABLE tasks ADD COLUMN size TEXT")
    asked = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
    if asked and "delivered_at" not in asked:
        conn.execute("ALTER TABLE questions ADD COLUMN delivered_at TEXT")
        conn.execute("UPDATE questions SET delivered_at=answered_at WHERE status='answered'")  # answered before answers were tracked: given then


def take_answers(conn, sid, stamp):
    """The user's answers to session `sid`'s questions that it has not been given yet, each claimed for the caller (the
    worker reading them, or the watcher about to prompt it) in the same step, so no answer is given twice: rows of
    (id, question, answer). The caller commits."""
    rows = conn.execute("SELECT id, question, answer FROM questions WHERE session_id=? AND status='answered' AND delivered_at IS NULL "
                        "ORDER BY id", (sid,)).fetchall()
    return [r for r in rows
            if conn.execute("UPDATE questions SET delivered_at=? WHERE id=? AND delivered_at IS NULL", (stamp, r["id"])).rowcount]


def answer_line(q, answer=None):
    """How an answer is handed to the worker that asked: its question's id and first line, then the answer verbatim."""
    return f"Answer from the user to your question Q{q['id']} ({q['question'].splitlines()[0][:120]}): {q['answer'] if answer is None else answer}"


PROFILE_ENTRY = "profile"  # the top-level directory holding a profile inside a Herdwright checkout
CORE_MARKER = "profile.template"  # present in a Herdwright checkout only


def core_tree(worktree, rev="HEAD"):
    """A hash of what `rev` holds apart from the profile: a verdict is about this, not about the commit, so a commit
    that touches only `profile/` keeps it and any other change replaces it. In a Herdwright checkout (it has
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


def session_scratch(conn, worktree):
    """The scratch folders of the sessions recorded on `worktree`: an in-place project holds them all in one
    checkout. A closed session's folder is among them while it is there (`close` keeps one that holds unique work,
    and no longer records one it removed)."""
    return [r["scratch"] for r in conn.execute("SELECT scratch FROM sessions WHERE worktree=? AND scratch IS NOT NULL",
                                               (str(worktree),))]


# Folders a test run or a linter leaves behind. A project's `.gitignore` usually names them; one that does not must
# not stall a task on them. NO_BYTECODE keeps Python from writing the first one at all.
CACHE_FOLDERS = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")
NO_BYTECODE = {"PYTHONDONTWRITEBYTECODE": "1"}


def uncommitted(worktree):
    """The worktree's uncommitted changes and untracked files as git's short status lists them, (code, path), a
    folder with no tracked file in it as one path that ends in `/` (pinned: the user's `status.showUntrackedFiles`
    may say otherwise)."""
    out = git(worktree, "status", "--porcelain", "-z", "--untracked-files=normal").stdout.split("\0")
    entries, skip = [], False
    for entry in out:
        if skip or not entry:  # the second path of a rename
            skip = False
            continue
        entries.append((entry[:2], entry[3:]))
        skip = "R" in entry[:2] or "C" in entry[:2]  # staged, or seen in the worktree only
    return entries


def is_cache_leftover(code, path):
    """Whether an entry of `uncommitted` is a cache folder (CACHE_FOLDERS) that holds no tracked file: a leftover, not
    work. A tracked file in a folder of such a name is code like any other."""
    return code == "??" and path.endswith("/") and Path(path).name in CACHE_FOLDERS


def cache_leftovers(worktree):
    """The cache folders in the worktree that are leftovers (is_cache_leftover)."""
    return [path for code, path in uncommitted(worktree) if is_cache_leftover(code, path)]


def left_out_entries(worktree, scratch=()):
    """The entries at the worktree's top that are not its code: the profile and the folders `scratch` that lie there."""
    return [PROFILE_ENTRY, *sorted({Path(d).name for d in scratch if Path(d).parent == Path(worktree)})]


def changes(worktree, left_out=()):
    """The paths of the worktree's uncommitted changes and untracked files that are work: the cache leftovers left
    out, and what lies under the top entries `left_out` (for a plan session: left_out_entries, as `worktree_state`
    compares; for a worktree about to be removed: nothing, all of it would be lost)."""
    return [path for code, path in uncommitted(worktree)
            if not is_cache_leftover(code, path) and Path(path).parts[0] not in left_out]


def worktree_state(worktree, scratch=()):
    """A hash of what lies in the worktree apart from the profile, committed or not: `core_tree` of HEAD with every
    uncommitted change and untracked file (ignored files excepted, the cache folders test runs leave behind, see
    `cache_leftovers`, and the folders `scratch` at its top: the scratch
    folders of the sessions recorded on it, see `session_scratch`; a folder merely named like one is not left out). A
    plan session is held to it, because it writes the plan file only. The tree is written from a copy of the index
    into an object store of its own, so the checkout's index and objects stay as they are. None when the worktree is
    not a git checkout."""
    head = core_tree(worktree)
    index, objects = (git(worktree, "rev-parse", "--path-format=absolute", "--git-path", name) for name in ("index", "objects"))
    if head is None or not (index.ok and objects.ok):
        return None
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ | {"GIT_INDEX_FILE": str(Path(tmp) / "index"), "GIT_OBJECT_DIRECTORY": str(Path(tmp) / "objects"),
                            "GIT_ALTERNATE_OBJECT_DIRECTORIES": objects.stdout.strip()}
        Path(env["GIT_OBJECT_DIRECTORY"]).mkdir()
        if Path(index.stdout.strip()).is_file():
            shutil.copy2(index.stdout.strip(), env["GIT_INDEX_FILE"])
        added = run_bounded(["git", "-C", str(worktree), "add", "-A", "--", ".",
                             *[f":(exclude){name}" for name in left_out_entries(worktree, scratch)],
                             *[f":(exclude,literal){path}" for path in cache_leftovers(worktree)]], env=env)
        tree = run_bounded(["git", "-C", str(worktree), "write-tree"], env=env)
    if not (added.ok and tree.ok):
        return None
    return hashlib.sha256(f"{head}\n{tree.stdout.strip()}".encode()).hexdigest()


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
# and QA. Say so explicitly with the marker `[after-review]` in the step's title, or start the title
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
# The step a worker can only tick by finishing: `hw-employee finish` ticks it itself. It is exactly the line the brief
# template writes as the plan's last step, `- [ ] Run hw-employee finish and report (HW:DONE)` (backticks, a leading
# "N." and a trailing "done when ..." or "~time" are ignored); any other wording is an ordinary step.
FINISH_STEP_TITLE = "run hw-employee finish and report (hw:done)"


def step_title(step):
    return re.split(r"done when", step["text"], maxsplit=1, flags=re.I)[0]


def step_label(step):
    return re.sub(r"^\d+\.\s*", "", step_title(step)).strip(" .:\u2014\u2013-")[:60]


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


def plan_steps_full(text):
    """parse_plan_steps, each step with `full`: its line and the indented lines that continue it, up to the next
    checklist item, heading or paragraph."""
    steps = parse_plan_steps(text)
    for st, nxt in zip(steps, steps[1:] + [None]):
        start = text.index(st["line"])
        end = text.index(nxt["line"], start + 1) if nxt else len(text)
        st["full"] = re.split(r"\n#{1,6} |\n\n\S", text[start:end])[0]
    return steps


DONE_WHEN = re.compile(r"done when", re.I)
# "~40 min", "(40 min)", "~2 h", "1.5 hours": the brief asks only for "a rough time", so the "~" is optional. A step's
# text may name other times ("cache for 24 h"): a "~" estimate wins, else the last one (the estimate ends the line).
ESTIMATE = re.compile(r"(~\s*)?\b(\d+(?:\.\d+)?)\s*(min|mins|minutes?|m|h|hr|hrs|hours?)\b", re.I)


def estimate_minutes(text):
    found = list(ESTIMATE.finditer(text))
    if not found:
        return None
    m = next((m for m in found if m.group(1)), found[-1])
    n = float(m.group(2))
    return int(n * 60) if m.group(3).lower().startswith("h") else int(n)


def plan_lint(steps):
    """What is wrong with the structure of a plan's steps (`plan_steps_full`), empty when nothing is: numbered in
    order and once, a "done when" and a time estimate on each, no catch-all tick."""
    problems = []
    if not steps:
        return ["no checklist steps found ('- [ ] 1. ...')"]
    seen = set()
    first = steps[0]["n"]  # plans may start at 0 or 1
    for i, st in enumerate(steps, first):
        # the brief's fixed last line, which hw-employee finish ticks; a fix round adds its steps below the ticked one
        fixed_last = is_finish_step(st) and (st is steps[-1] or st["done"])
        if st["n"] in seen:
            problems.append(f"step {st['n']}: duplicate number")
        seen.add(st["n"])
        if st["n"] != i:
            problems.append(f"step {st['n']}: out of order (expected {i})")
        if not DONE_WHEN.search(st["full"]) and not fixed_last:
            problems.append(f"step {st['n']}: no 'Done when'")
        if step_catch_all(st):
            problems.append(f"step {st['n']}: a catch-all tick ('1-5 done'): one checklist line per step, each with its own 'Done when'")
        if estimate_minutes(st["full"]) is None and not fixed_last:
            problems.append(f"step {st['n']}: no time estimate (like '~40 min')")
    return problems


# What a test-first plan holds before its steps (templates/skills/plan-change): the behaviour to protect with
# the test that pins each, and the new test per requirement. A plan with the first of them is held to all of this.
PLAN_TEST_SECTIONS = ("Existing behaviour to protect", "New tests")
STEP_TESTS = re.compile(r"\bTests:\s*\S", re.I)
STEP_EVIDENCE = re.compile(r"\bEvidence:\s*\S", re.I)


def plan_section(text, title):
    """The body of the plan's `## <title>` section, or None when it has no such heading."""
    m = re.search(rf"^#{{2,4}}\s+{re.escape(title)}[ \t]*$(.*?)(?=^#{{1,4}}\s|\Z)", text, re.M | re.S | re.I)
    return m.group(1) if m else None


def section_rows(body):
    """The lines of a section that say something: not blank, not a table's header or rule, not a template's `<…>` note."""
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    table_head = {i for i, l in enumerate(lines[:-1]) if re.fullmatch(r"\|?[\s:|-]+\|?", lines[i + 1]) and "-" in lines[i + 1]}
    return [l for i, l in enumerate(lines) if i not in table_head and i - 1 not in table_head and not re.fullmatch(r"<[^>]*>?|[^<]*>", l)]


def is_test_first_plan(text):
    return plan_section(text, PLAN_TEST_SECTIONS[0]) is not None


def plan_check_problems(text):
    """Why a plan file is not a test-first plan a build can follow (empty: it is): each test section is there and says
    something ("None: the repository has no tests" says something), its steps are well formed (`plan_lint`), every
    step but the merge/push/raise-PR ones and the finish line names its tests (`Tests: …`, or `Tests: none`), and
    every ticked one says where its evidence is (`Evidence: <test name or commit>`), so a later session does not redo
    it to find out. The one check of a plan: `hw-plan check`, a plan session's `finish` and `bin/hw plan check`."""
    problems = []
    for title in PLAN_TEST_SECTIONS:
        body = plan_section(text, title)
        if body is None:
            problems.append(f"no section '## {title}'")
        elif not section_rows(body):
            problems.append(f"section '## {title}' is empty: list its rows, or say \"None\" and why")
    steps = plan_steps_full(text)
    problems += plan_lint(steps)
    for st in steps:
        if step_exempt(st) or is_finish_step(st):
            continue
        if not STEP_TESTS.search(st["full"]):
            problems.append(f"step {st['n']}: names no tests ('Tests: <names>', or 'Tests: none' where the repository has none)")
        if st["done"] and not STEP_EVIDENCE.search(st["full"]):
            problems.append(f"step {st['n']}: ticked without 'Evidence: <test name or commit>'")
    return problems


def first_open_step(steps):
    """The first step a build still has to do (never a merge/push/raise-PR step or the finish line), or None."""
    return next((s for s in plan_open(steps)[0] if not is_finish_step(s)), None)


def brief_plan_file(brief_text):
    """The plan file a brief tells its worker to write (the first `...-plan.md` in its '## Plan' section), or None."""
    section = re.search(r"^## Plan\b(.*?)(?=^## |\Z)", brief_text, re.M | re.S)
    m = re.search(r"`([^`\s]+-plan\.md)`", section.group(1)) if section else None
    return Path(m.group(1)) if m else None


READER_PLAN = re.compile(r"T\d+-(review|qa)-S\d+-plan\.md")  # the file name `reader_plan_name` builds


def reader_plan_name(hat, task_id, sid):
    """A review's or QA's own plan file name, named like its findings: two readers on one task never share a plan."""
    return f"T{task_id}-{hat}-S{sid}-plan.md"


PLAN_HAT = "plan"  # writes the plan a build follows, and nothing else
# How much of the four steps (plan, build, qa, review) a task runs: `normal` all four, `small` without the plan
# session (its build writes the plan itself). A task with no size is from before the four steps and runs as small.
TASK_SIZES = ("normal", "small")


def plan_approval():
    """Whether the user asked to approve each plan before its build starts (`bin/hw configure --plan-approval on`)."""
    return settings().get("plan_approval") is True


def task_size(conn, task_id):
    """The task's size (TASK_SIZES), or None for a task from before the four steps."""
    task = conn.execute("SELECT size FROM tasks WHERE id=?", (task_id,)).fetchone()
    return task["size"] if task else None


def finish_event_text(sid, hat, verdict=None):
    """The text of a session's `finish` event, as `hw-employee finish` writes it and `written_plans` finds it."""
    return f"S{sid} {hat}" + (f" {verdict}" if verdict else "")


def written_plans(conn, task_id):
    """The task's plans that a plan session finished, latest first: per plan file the `sessions` row (a dict) of the
    latest plan session that wrote it, with `finished`, the id of its `finish` event. A task has one per brief."""
    latest = {}
    for s in conn.execute("SELECT * FROM sessions WHERE task_id=? AND hat=?", (task_id, PLAN_HAT)).fetchall():
        done = conn.execute("SELECT MAX(id) FROM events WHERE task_id=? AND kind='finish' AND text=?",
                            (task_id, finish_event_text(s["id"], PLAN_HAT))).fetchone()[0]
        if done and (s["plan"] not in latest or done > latest[s["plan"]]["finished"]):
            latest[s["plan"]] = dict(s) | {"finished": done}
    return sorted(latest.values(), key=lambda s: -s["finished"])


def plan_event_text(plan, said):
    """The text of a `plan-check` or `plan-approve` event: it begins with the plan file it is about."""
    return f"{Path(plan).name} {said}"


def build_wait_problem(conn, task_id, plan):
    """Why a build may not start on `plan` (the plan file it will follow) yet, or None: on a `normal` task a plan
    session must have finished on that file, then Herdwright recorded its check of that plan against the brief, then
    the user approved it when they switched approval on. The check and the approval of another plan of the task (a
    task may have a brief per repository) do not count, nor do those from before the plan was last written."""
    if task_size(conn, task_id) != "normal":
        return None
    plans = written_plans(conn, task_id)
    planned = next((s for s in plans if session_plan(s) == Path(plan)), None)
    if not planned:
        return (f"no plan session has finished on T{task_id} for {Path(plan).name}: spawn one with hat={PLAN_HAT} first. "
                f"A small task skips it (`bin/hw task set T{task_id} size=small`): its build writes the plan itself")
    which = f"T{task_id}" + (f" repo={planned['repo']}" if len(plans) > 1 else "")
    def recorded(kind):
        return any(r["text"].startswith(plan_event_text(plan, "")) for r in conn.execute(
            "SELECT text FROM events WHERE task_id=? AND kind=? AND id>?", (task_id, kind, planned["finished"])))
    if not recorded("plan-check"):
        return (f"the plan {Path(plan).name} of S{planned['id']} has not been checked against the brief: read both, then "
                f"`bin/hw plan check {which} ok`")
    if plan_approval() and not recorded("plan-approve"):
        return (f"plan approval is on and the user has not approved this plan: show it to them, then "
                f"`bin/hw plan approve {which} \"<their words>\"`")
    return None


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
    """The last step when it is the unticked `Run hw-employee finish and report (HW:DONE)` line, else None."""
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
    """Why the task's build is not complete enough for review/QA, or None. A task of the four steps (it has a size,
    `normal` or `small`) needs a build to read: a written plan is not one. A task from before them with no recorded
    build plan is not gated here (`spawn` demands a plan, `hw-plan lint` checks it); only sessions older than
    sessions.plan lack one."""
    path = gate_plan(conn, task_id)
    if path:
        return open_steps_problem(read_steps(path), f"the build plan of T{task_id} ({path.name})")
    if task_size(conn, task_id):
        return f"no build has run on T{task_id}: spawn one with hat=build first, then review and qa read what it made"
    return None


def session_plan_problem(s, skip_finish=False):
    """Why the session's own plan is not complete, or None; with skip_finish the 'run finish / report' step is left to finish.
    A plan session's plan is complete when it is written as a build can follow it: its steps are the build's to tick."""
    path = session_plan(s)
    if s["hat"] == PLAN_HAT:
        if not (path and path.is_file()):
            return f"S{s['id']} has not written the plan file {path}"
        problems = plan_check_problems(path.read_text())
        return f"S{s['id']}'s plan ({path.name}) is not one a build can follow: " + "; ".join(problems[:6]) if problems else None
    steps = read_steps(path)
    last = finish_step(steps) if skip_finish else None
    return open_steps_problem(steps, f"S{s['id']}'s plan ({path.name})" if path else "", [last] if last else [])


def tick_step(path, step):
    """Tick one step of a plan file in place."""
    text = Path(path).read_text()
    Path(path).write_text(text.replace(step["line"], step["line"].replace("[ ]", "[x]", 1), 1))

