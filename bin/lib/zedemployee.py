"""Employee records: hire, list, show, intro, skill lookup and hat manifests (design section 5).

An employee is files under profile/employees/<name>/ (employee.md and memory/, see zedmem.py); the `employees` table
in the task DB is only an index. Core ships the hat templates and roster cards in templates/employees/; the profile
holds the instances. No model is involved.
"""
import json
import re
import sqlite3
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


def _scalar(value):
    value = value.strip()
    if value[:1] in ('"', "'"):
        return json.loads(value) if value[0] == '"' else value[1:-1]
    value = re.sub(r"\s+#.*$", "", value)
    if value.startswith("["):
        return [v.strip() for v in value.strip("[]").split(",") if v.strip()]
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
            return "[" + ", ".join(v) + "]"
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
    if not isinstance(deny, dict) or set(deny) != {"edit", "commands"} \
            or not all(isinstance(deny[k], list) and all(isinstance(x, str) for x in deny[k]) for k in deny):
        problems.append("deny: must be {\"edit\": [globs], \"commands\": [prefixes]}")
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


def _conn():
    DB = zedlib.DB_PATH
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    zedlib.ensure_employee_schema(conn)
    return conn


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
    with _conn() as conn:
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
        with _conn() as conn:
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
    with _conn() as conn:
        conn.execute("UPDATE employees SET introduced_at = ? WHERE name = ?", (today or date.today().isoformat(), name))
