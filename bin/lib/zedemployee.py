"""Employee records: hire, list, show, intro, skill lookup and hat manifests (design section 5).

An employee is files under profile/employees/<name>/ (employee.md and memory/, see zedmem.py); the `employees` table
in the task DB is only an index. Core ships the hat templates and roster cards in templates/employees/; the profile
holds the instances. No model is involved.
"""
import contextlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zedlib  # noqa: E402
import zedmem  # noqa: E402

TEMPLATES = zedlib.ZED_HOME / "templates" / "employees"
NAME = re.compile(r"[a-z][a-z0-9-]*")
SUBAGENT_MODES = ["none", "read-only", "free"]
MANIFEST_LISTS = ["scripts", "skills"]
HAT_CHARTER_WORDS = 150


class EmployeeError(Exception):
    pass


def employees_dir():
    return zedlib.PROFILE_DIR / "employees"


def employee_dir(name):
    return employees_dir() / name


# ---------------------------------------------------------------- frontmatter


LIST_ITEM = re.compile(r'\s*("(?:[^"\\]|\\.)*"|[^,]*)\s*(?:,|$)')


def _item(text):
    text = text.strip()
    return json.loads(text) if text[:1] == '"' else text


def _scalar(value):
    value = value.strip()
    if value[:1] in ('"', "'"):
        return json.JSONDecoder().raw_decode(value)[0] if value[0] == '"' else value[1:-1]
    if m := re.match(r"\[(.*)\]\s*(?:#.*)?$", value):
        return [_item(v) for v in LIST_ITEM.findall(m.group(1)) if v.strip()]
    value = re.sub(r"\s+#.*$", "", value)
    if value in ("true", "false"):
        return value == "true"
    return int(value) if re.fullmatch(r"-?\d+", value) else value


def parse_frontmatter(text):
    """(fields, body) for a markdown file opening with a `---` block of `key: value` lines."""
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
    if not m:
        return {}, text
    fields = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not line.startswith("#"):
            fields[key.strip()] = _scalar(value)
    return fields, m.group(2).strip()


def render_frontmatter(fields):
    def fmt(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, list):
            return "[" + ", ".join(json.dumps(i) if re.search(r"[,\[\]#\"]|^\s|\s$", i) else i for i in v) + "]"
        return json.dumps(v) if isinstance(v, str) and re.search(r"[:#\"]|^\s|\s$", v) else str(v)
    return "---\n" + "".join(f"{k}: {fmt(v)}\n" for k, v in fields.items()) + "---\n"


# ---------------------------------------------------------------- hats and skills


def hat_names():
    return sorted(p.name for p in (TEMPLATES / "hats").iterdir() if p.is_dir()) if (TEMPLATES / "hats").is_dir() else []


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
            if skill_path(skill, hat=Path(hat_dir).name) is None:
                problems.append(f"skill {skill!r} has no SKILL.md")
    return problems


def load_manifest(hat):
    path = TEMPLATES / "hats" / hat / "capabilities.json"
    if not path.exists():
        raise EmployeeError(f"unknown hat {hat!r}: one of {', '.join(hat_names())}")
    return json.loads(path.read_text())


def skill_path(name, name_of_employee=None, hat=None):
    """The SKILL.md for a skill: the employee's own first, then the hat's (any hat when none is given), then the
    ones every hat shares."""
    candidates = []
    if name_of_employee:
        candidates.append(employee_dir(name_of_employee) / "skills" / name / "SKILL.md")
    for h in [hat] if hat else hat_names():
        candidates.append(TEMPLATES / "hats" / h / "skills" / name / "SKILL.md")
    candidates.append(TEMPLATES / "common" / "skills" / name / "SKILL.md")
    return next((p for p in candidates if p.exists()), None)


def skill_names(hat=None):
    names = {p.parent.name for p in (TEMPLATES / "common" / "skills").glob("*/SKILL.md")}
    for h in [hat] if hat else hat_names():
        names |= {p.parent.name for p in (TEMPLATES / "hats" / h / "skills").glob("*/SKILL.md")}
    return sorted(names)


# ---------------------------------------------------------------- records


def roster_names():
    return sorted(p.stem for p in (TEMPLATES / "roster").glob("*.md"))


def load_employee(name):
    path = employee_dir(name) / "employee.md"
    if not path.exists():
        raise EmployeeError(f"no employee {name!r} (hired: {', '.join(names()) or 'nobody'})")
    return parse_frontmatter(path.read_text())


def names():
    d = employees_dir()
    return sorted(p.parent.name for p in d.glob("*/employee.md")) if d.is_dir() else []


def connect():
    DB = zedlib.DB_PATH
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    zedlib.ensure_employee_schema(conn)
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


def hire(name, *, voice=None, strengths=None, senior=None, today=None):
    """Create profile/employees/<name>/ from the roster card (or from a blank card: voice required), register the
    DB row. Returns the employee's directory. Refuses an existing name."""
    if not NAME.fullmatch(name):
        raise EmployeeError("a name is lowercase letters, digits and dashes, starting with a letter")
    edir = employee_dir(name)
    if edir.exists():
        raise EmployeeError(f"{name} is already hired")
    card = TEMPLATES / "roster" / f"{name}.md"
    fields, body = parse_frontmatter(card.read_text()) if card.exists() else ({}, "")
    voice = voice or fields.get("voice")
    if not voice:
        raise EmployeeError(f"{name} is not in the roster ({', '.join(roster_names())}): give --voice for a blank card")
    strengths = strengths if strengths is not None else fields.get("strengths", [])
    senior = bool(fields.get("senior", False) if senior is None else senior)
    today = today or date.today().isoformat()
    record = {"name": name, "voice": voice, "strengths": strengths, "senior": senior, "hired": today,
              "runtime": "auto", "memory_words": zedmem.DEFAULT_MEMORY_WORDS}
    edir.mkdir(parents=True)
    (edir / "employee.md").write_text(render_frontmatter(record) + f"# {name.capitalize()}\n\n{body}\n")
    for hat in ["general"] + hat_names():
        zedmem.save(edir / "memory" / "craft" / f"{hat}.md", f"# Lessons: {hat}", [])
    (edir / "memory" / "repos").mkdir(parents=True)
    (edir / "memory" / "repos" / ".gitkeep").touch()
    (edir / "log.md").write_text(f"# {name.capitalize()}: assignment log\n\nGenerated by zed-employee finish; never hand-edited.\n")
    with database() as conn:
        conn.execute("INSERT OR IGNORE INTO employees (name, senior, hired_at) VALUES (?, ?, ?)",
                     (name, int(senior), today))
    return edir


def memory_counts(name):
    counts = {k: 0 for k in zedmem.KINDS}
    for _, e in zedmem.all_entries(employee_dir(name)):
        counts[e.kind] += 1
    return counts


def list_employees():
    """[{name, senior, hired, introduced, memory}] from the files, with `introduced` from the DB index."""
    intro = {}
    if zedlib.DB_PATH.exists():
        with database() as conn:
            intro = {r["name"]: r["introduced_at"] for r in conn.execute("SELECT name, introduced_at FROM employees")}
    out = []
    for n in names():
        f, _ = load_employee(n)
        out.append({"name": n, "senior": bool(f.get("senior")), "hired": f.get("hired"),
                    "introduced": intro.get(n), "memory": sum(memory_counts(n).values())})
    return out


def intro_card(name):
    """The introduction card as printed lines, from the files only."""
    f, body = load_employee(name)
    return [
        f"{name.capitalize()}" + (" (senior: first pick for design and ambiguous work, no extra authority)" if f.get("senior") else ""),
        f"  Personality: {f.get('voice')}",
        f"  Strengths: {', '.join(f.get('strengths', [])) or 'none listed'} (habits, not roles)",
        f"  Hats they wear: {', '.join(hat_names())}; any employee can wear any hat",
        "  Never without approval: decide product, team-lead or architecture questions; post anything outward except "
        "their own PR; review or QA work they authored",
        "  Remembers: facts, preferences, lessons per hat, work, things heard. Each entry has its source and a date; "
        "facts about code are re-checked against the repo",
    ]


def record_intro(name, today=None):
    load_employee(name)
    with database() as conn:
        conn.execute("UPDATE employees SET introduced_at = ? WHERE name = ?", (today or date.today().isoformat(), name))


# ---------------------------------------------------------------- what an assignment starts with


def pack_path(name, session):
    return zedlib.PROFILE_DIR / ".cache" / "employees" / name / f"{session or 'adhoc'}-pack.md"


def make_pack(name, repo, hat, tags=(), session=None, memory_words=None, dry_run=False):
    """Verify the repo's entries, then build the memory pack. Returns (text, path, used_ids, archived_ids); unless
    dry_run the pack is written to profile/.cache/employees/<name>/<session>-pack.md and `uses` are recorded."""
    if hat not in hat_names():
        raise EmployeeError(f"unknown hat {hat!r}: one of {', '.join(hat_names())}")
    edir = employee_dir(name)
    fields, _ = load_employee(name)
    zedmem.verify(edir, repo)
    text, used, attention = zedmem.build_pack(edir, name, repo=repo, hat=hat, tags=tags,
                                              memory_words=memory_words or fields.get("memory_words", zedmem.DEFAULT_MEMORY_WORDS))
    if dry_run:
        return text, None, used, []
    archived = zedmem.record_pack(edir, repo, used, attention)
    path = pack_path(name, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return text, path, used, archived


def employee_rules(name, hat, pack):
    """The text appended to the worker rules for an employee's session: the hat's charter, then the employee rules
    with the name, voice, hat and the pack's path filled in."""
    fields, _ = load_employee(name)
    charter = (TEMPLATES / "hats" / hat / "hat.md").read_text().strip()
    rules = (TEMPLATES / "employee-rules.md").read_text()
    for key, value in {"NAME": name.capitalize(), "VOICE": fields.get("voice", ""), "HAT": hat, "PACK": str(pack)}.items():
        rules = rules.replace("{{" + key + "}}", value)
    return charter + "\n\n" + rules.strip() + "\n"


# ---------------------------------------------------------------- finish (write-back)

VERDICTS = ["clean", "blockers"]
VERDICT_HATS = ("review", "qa")
IDEAS_QUESTION = "Did you do anything by hand more than once that a script could do? Add `--idea \"...\"` for each."


CODE_FIRST_QUESTION = ("A lesson in prose only repeats the mistake: can any of it be a script, hook or check? "
                       "Add `--idea \"...\"` for each that can (it goes to ideas.md for Zed to build), "
                       "or `--no-idea \"why none can\"`.")


class FinishRefused(zedmem.MemoryRefused):
    """`finish` refused: the message says what to add before running it again."""


def head_sha(worktree):
    """The commit a verdict is about: HEAD of the session's worktree (None when it is not a git checkout)."""
    r = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def stop_problem(session=None, name=None):
    """Why a worker's turn may not end yet: it has reported `done:` in its status file but has not run `finish`
    (the status file is the one report both runtimes' workers write before ZED:DONE). None when it may stop.
    Fails open when the session or its files cannot be read: a hook must never wedge a turn."""
    sid = str(session or "").lstrip("S")
    if not (name and sid.isdigit()):
        return None
    log = zedlib.PROFILE_DIR / ".cache" / "status" / f"S{sid}.log"
    if not log.is_file() or not re.search(r"^done:", log.read_text(errors="replace"), re.M):
        return None
    with database() as conn:
        if conn.execute("SELECT 1 FROM events WHERE kind='finish' AND text LIKE ?", (f"S{sid} %",)).fetchone():
            return None
    return (f"S{sid} reported done but `zed-employee finish` has not run: run it (it writes back what you learned and "
            "the verdict's head sha), then print your ZED:DONE block")


def place_draft(draft, tid, name, sid):
    """Copy a findings draft to profile/briefs/T<task>-<name>-S<session>-findings.md (where a review/qa hat may write
    it, and where whoever fixes looks) unless it is already there; returns the path the record should name."""
    dest = zedlib.PROFILE_DIR / "briefs" / f"T{tid}-{name}-S{sid}-findings.md"
    if Path(draft).resolve() != dest.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(draft, dest)
    return str(dest)


def open_assignment(conn, name, session=None):
    """The session `finish` is about: the one named (S42 or 42), else the employee's only open one."""
    if session:
        row = conn.execute("SELECT * FROM sessions WHERE id=? AND employee=?", (int(str(session).lstrip("S")), name)).fetchone()
        if not row:
            raise EmployeeError(f"{session} is not an assignment of {name}")
        return row
    rows = conn.execute("SELECT * FROM sessions WHERE employee=? AND status IN ('running','waiting_user') ORDER BY id", (name,)).fetchall()
    if len(rows) != 1:
        raise EmployeeError(f"{name} has {len(rows)} open assignments: say which with --session S<id>" if rows
                            else f"{name} has no open assignment")
    return rows[0]


def plan_progress(tid):
    done = total = 0
    for plan in sorted((zedlib.PROFILE_DIR / "briefs").glob(f"T{tid}-*-plan.md")):
        steps = zedlib.parse_plan_steps(plan.read_text())
        done, total = done + sum(s["done"] for s in steps), total + len(steps)
    return done, total


def finish(name, *, verdict=None, lessons=(), none_reason=None, draft=None, ideas=(), note=None, session=None, today=None):
    """Write back an assignment, no model. Refuses (FinishRefused) before changing anything unless: a review/qa has a
    verdict (and a findings draft when it is `blockers`), a build has none; there is at least one lesson or a
    `none_reason`; no fact of the repo is still stale; the session was not finished before. Then: the lessons go to
    the hat's craft file, the `work` entry is written from the DB and the plan, the verdict is stored on the
    session, the findings draft is recorded for whoever fixes, one line goes to log.md, ideas to ideas.md.
    Returns the report lines (deterministic for the same inputs and files)."""
    today = today or date.today().isoformat()
    edir = employee_dir(name)
    load_employee(name)
    with database() as conn:
        s = open_assignment(conn, name, session)
        sid, tid, hat, repo = s["id"], s["task_id"], s["hat"], s["repo"]
        if conn.execute("SELECT 1 FROM events WHERE kind='finish' AND text LIKE ?", (f"S{sid} %",)).fetchone():
            raise FinishRefused(f"S{sid} is already finished")
        if hat in VERDICT_HATS:
            if verdict not in VERDICTS:
                raise FinishRefused(f"a {hat} assignment needs --verdict {'|'.join(VERDICTS)}")
        elif verdict:
            raise FinishRefused(f"--verdict is for {' and '.join(VERDICT_HATS)} assignments; this one wore {hat}")
        if draft:
            draft = str(Path(draft).expanduser().resolve())
            if not Path(draft).is_file():
                raise FinishRefused(f"findings draft {draft} does not exist")
        elif verdict == "blockers":
            raise FinishRefused("a verdict of blockers needs --draft <findings file>: whoever fixes starts from it")
        sha = head_sha(s["worktree"]) if verdict else None
        if verdict and not sha:
            raise FinishRefused(f"cannot read the head commit of {s['worktree']}: a verdict is recorded with the sha it is about")
        lessons = [x for x in lessons if x.strip()]
        if bool(lessons) == bool(none_reason):
            raise FinishRefused("give at least one --lesson \"...\", or --none \"why nothing new\" (not both)")
        repo_file = zedmem.memory_dir(edir) / "repos" / f"{repo}.md"
        stale = [e.id for path, e in zedmem.all_entries(edir) if path == repo_file and e.get("stale")]
        if stale:
            raise FinishRefused(f"stale memory entries of {repo} are still unresolved: {' '.join(stale)}; "
                                "`zed-employee refresh <id>` or `drop <id> --reason ...` each, then finish")
        for text in [*lessons, note or "", *ideas]:
            if secret := zedlib.find_secret(text):
                raise FinishRefused(f"looks like a secret ({secret}): credentials are never written down")
        seen = []
        for text in lessons:
            zedmem.check_text(edir, text)
            if zedmem.normalise(text) in seen:
                raise FinishRefused("the same lesson twice")
            seen.append(zedmem.normalise(text))

        task = conn.execute("SELECT title FROM tasks WHERE id=?", (tid,)).fetchone()
        done, total = plan_progress(tid)
        outcome = f"verdict {verdict}" if verdict else f"PR {s['pr_url']}" if s["pr_url"] else "no PR yet"
        body = f"S{sid} {hat} on T{tid} ({(task['title'] if task else '')[:60]}), branch {s['branch']}: {outcome}; plan {done}/{total} steps." + \
               (f" {note.strip()}" if note and note.strip() else "")
        written = [zedmem.remember(edir, "lesson", text, f"T{tid} S{sid}", hat=hat).id for text in lessons]
        work = zedmem.remember(edir, "work", body, f"T{tid} S{sid}" + (f" {s['pr_url']}" if s["pr_url"] else ""), repo=repo, hat=hat)
        if verdict:
            conn.execute("UPDATE sessions SET verdict=?, verdict_sha=? WHERE id=?", (verdict, sha, sid))
        if draft:
            draft = place_draft(draft, tid, name, sid)
            conn.execute("INSERT INTO events(task_id, ts, kind, text) VALUES (?,?,?,?)",
                         (tid, today, "findings", f"S{sid} {name} {hat} {verdict or 'notes'}: {draft}"))
        conn.execute("INSERT INTO events(task_id, ts, kind, text) VALUES (?,?,?,?)",
                     (tid, today, "memory", f"S{sid} {name}: work {work.id}" + "".join(f", lesson {i}" for i in written)))
        conn.execute("INSERT INTO events(task_id, ts, kind, text) VALUES (?,?,?,?)",
                     (tid, today, "finish", f"S{sid} {name} {hat}" + (f" {verdict}" if verdict else "")))
    log = edir / "log.md"
    with log.open("a") as f:
        f.write(f"- {today} | S{sid} | T{tid} | {hat} | {repo} | {s['branch']} | {verdict or '-'} | {s['pr_url'] or '-'} | plan {done}/{total}\n")
    if ideas:
        path = employees_dir() / "ideas.md"
        head = "" if path.exists() else "# Ideas: a script could do this\n\nAppended by zed-employee finish; Zed turns these into code.\n\n"
        with path.open("a") as f:
            f.write(head + "".join(f"- {today} | {name} | S{sid} | {i.strip()}\n" for i in ideas))
    lines = [f"finished S{sid} ({name}, {hat}, T{tid})", f"  work entry {work.id}" + "".join(f", lesson {i}" for i in written),
             f"  plan {done}/{total} steps ticked" + ("" if done == total else " (not all: say why in your ZED:DONE)")]
    if verdict:
        lines.append(f"  verdict {verdict}" + (f", findings {draft}" if draft else ""))
    lines.append("  log.md line appended" + (f", {len(ideas)} idea(s) added to ideas.md" if ideas else ""))
    if not ideas:
        lines.append(IDEAS_QUESTION)
    return lines
