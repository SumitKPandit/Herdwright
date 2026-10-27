"""Employee memory: a strict markdown format that scripts can parse, verify, sort and cap without a model.

Design: docs/design/employees.md (branch design/zed-employees), section 5. An employee's memory lives in
profile/employees/<name>/memory/:

    craft/<hat>.md, craft/general.md   lessons (how to wear a hat, or work, better)
    repos/<repo>.md                    facts, preferences, work and heard entries for one repo / system
    archive.md                         dropped entries, each with its reason

One entry is a header line plus an indented body:

    - [m-0007] fact | 2026-10-20 | tags: auth,views | src: app/auth/guard.py anchor: "def require_role" | verified: 2026-10-27 | ttl: 60d | uses: 4
      Role checks live in `guard.py`, not in views.

Memory is written only through remember / refresh / drop (and verify / pack, which maintain the bookkeeping
fields). Every function takes the employee directory and an optional `today`, so tests need no clock.
No model is involved anywhere in this module.
"""
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import zedlib  # noqa: E402

KINDS = ["fact", "preference", "lesson", "work", "heard"]
DEFAULT_TTL = {"fact": 60, "preference": 180, "work": 180, "heard": 30}  # lessons never expire
MAX_WORDS = 120
LESSON_PACK_WORDS = 400
WORK_PACK_WORDS = 150
MAX_UNVERIFIED = 5
UNRESOLVED_LIMIT = 2  # a stale entry listed in this many packs without refresh/drop is archived
DEFAULT_MEMORY_WORDS = 1500

# header fields in the order they are written
FIELD_ORDER = ["tags", "hat", "src", "check", "verified", "ttl", "uses", "conflicts", "stale", "unresolved", "dropped"]
HEADER = re.compile(r"^- \[(m-\d+)\] (\w+) \| (\d{4}-\d{2}-\d{2})(?: \| (.*))?$")
PIPE_OUTSIDE_QUOTES = re.compile(r' \| (?=(?:[^"]*"[^"]*")*[^"]*$)')
SRC = re.compile(r'^(?P<path>.*?)(?: anchor: "(?P<anchor>[^"]*)")?$')


class MemoryRefused(Exception):
    """A refused write: the message says why, so the employee can fix the entry."""


class RepoUnavailable(MemoryRefused):
    """The repo or its base branch cannot be read, so anchors cannot be checked."""


# ---------------------------------------------------------------- entries


class Entry:
    def __init__(self, id, kind, date_, fields, body):
        self.id, self.kind, self.date, self.fields, self.body = id, kind, date_, dict(fields), body.strip()

    def get(self, key, default=None):
        return self.fields.get(key, default)

    @property
    def src_path(self):
        return SRC.match(self.get("src", "")).group("path")

    @property
    def anchor(self):
        return SRC.match(self.get("src", "")).group("anchor")

    @property
    def tags(self):
        return [t for t in self.get("tags", "").split(",") if t]

    @property
    def uses(self):
        return int(self.get("uses", 0))

    @property
    def words(self):
        return len(self.body.split())

    def header(self):
        parts = [f"- [{self.id}] {self.kind} | {self.date}"]
        for key in FIELD_ORDER:
            if self.fields.get(key) not in (None, ""):
                parts.append(f"{key}: {self.fields[key]}")
        return " | ".join(parts)

    def render(self):
        body = "\n".join("  " + ln if ln else "" for ln in self.body.splitlines())
        return f"{self.header()}\n{body}"


def parse_entries(text):
    """(preamble, [Entry]). Anything before the first entry is the preamble and is kept verbatim."""
    preamble, entries = [], []  # entries: [(Entry, body lines)] until the loop ends
    cur = None
    for line in text.splitlines():
        m = HEADER.match(line)
        if m:
            fields = {}
            for part in PIPE_OUTSIDE_QUOTES.split(m.group(4) or ""):
                key, _, value = part.partition(": ")
                if key:
                    fields[key.strip()] = value.strip()
            cur = []
            entries.append((Entry(m.group(1), m.group(2), m.group(3), fields, ""), cur))
        elif entries:
            cur.append(line[2:] if line.startswith("  ") else line)
        else:
            preamble.append(line)
    for entry, lines in entries:
        entry.body = "\n".join(lines).strip()
    entries = [e for e, _ in entries]
    return "\n".join(preamble).rstrip("\n"), entries


def render_entries(preamble, entries):
    chunks = ([preamble] if preamble else []) + [e.render() for e in entries]
    return "\n".join(chunks) + "\n" if chunks else ""


# ---------------------------------------------------------------- files


def memory_dir(edir):
    return Path(edir) / "memory"


def entry_file(edir, kind, repo=None, hat=None):
    """Where an entry of this kind lives: lessons by hat (or general), everything else by repo."""
    if kind == "lesson":
        return memory_dir(edir) / "craft" / f"{hat or 'general'}.md"
    if not repo:
        raise MemoryRefused(f"a {kind} entry needs --repo (facts, preferences, work and heard live per repo)")
    return memory_dir(edir) / "repos" / f"{repo}.md"


def all_files(edir):
    mem = memory_dir(edir)
    return sorted(p for sub in ("craft", "repos") for p in (mem / sub).glob("*.md")) if mem.is_dir() else []


def load(path):
    path = Path(path)
    return parse_entries(path.read_text() if path.exists() else "")


def save(path, preamble, entries):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_entries(preamble, entries))


def all_entries(edir, include_archive=False):
    """[(path, Entry)] across every memory file (archive.md only on request)."""
    files = all_files(edir)
    archive = memory_dir(edir) / "archive.md"
    if include_archive and archive.exists():
        files.append(archive)
    return [(p, e) for p in files for e in load(p)[1]]


def find(edir, entry_id):
    for path, entry in all_entries(edir):
        if entry.id == entry_id:
            return path, entry
    raise MemoryRefused(f"no entry {entry_id}")


def next_id(edir):
    top = max((int(e.id[2:]) for _, e in all_entries(edir, include_archive=True)), default=0)
    return f"m-{top + 1:04d}"


def archive(edir, entry, reason, today=None):
    path = memory_dir(edir) / "archive.md"
    preamble, entries = load(path)
    entry.fields["dropped"] = f"{today or _today()}: {reason}"
    entries.append(entry)
    save(path, preamble or "# Archive\n\nDropped entries with the reason. Git has the rest.", entries)


# ---------------------------------------------------------------- helpers


def _today():
    return date.today().isoformat()


def _days_between(earlier, later):
    return (datetime.strptime(later, "%Y-%m-%d") - datetime.strptime(earlier, "%Y-%m-%d")).days


def normalise(text):
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", text.lower())).strip()


def ttl_days(entry):
    value = entry.get("ttl")
    return int(value.rstrip("d")) if value else None


def stamp_date(entry):
    """The date an entry was last known good: its verification date, else the day it was written."""
    return entry.get("verified") or entry.date


def is_expired(entry, today):
    ttl = ttl_days(entry)
    return ttl is not None and _days_between(stamp_date(entry), today) > ttl


def anchor_in_base(repo_reader, repo, path, anchor):
    """True when the anchor text occurs in the file on the repo's base branch now. repo_reader(repo, path) -> text|None."""
    text = repo_reader(repo, path)
    return text is not None and anchor in text


def git_reader(repo_cfg_lookup=None):
    """A repo_reader reading `git show origin/<dev_base>:<path>` in the repo named by projects.json. Returns None when
    the file is not on that branch; raises RepoUnavailable when the repo or its base branch cannot be read at all
    (so an unreadable repo never makes every fact look stale)."""
    lookup = repo_cfg_lookup or zedlib.projects

    def read(repo, path):
        cfg = lookup().get(repo)
        if not cfg:
            raise RepoUnavailable(f"repo {repo!r} is not in config/projects.json")
        base = cfg.get("dev_base") or "main"
        root = zedlib.expand(cfg["path"])
        for ref in (f"origin/{base}", base):
            if zedlib.git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").ok:
                r = zedlib.git(root, "show", f"{ref}:{path}")
                return r.stdout if r.ok else None
        raise RepoUnavailable(f"no branch origin/{base} or {base} in {root}")

    return read


# ---------------------------------------------------------------- remember / refresh / drop


def _check_src(kind, src, anchor, repo, repo_reader):
    """Validate src per kind; returns (stored src string, fields to add)."""
    src = (src or "").strip()
    if not src:
        raise MemoryRefused("src is mandatory: cite the file, URL, decision, PR, assignment or message it came from")
    extra = {}
    if kind == "heard":
        if not re.fullmatch(r"msg #\d+", src):
            raise MemoryRefused("a heard entry's src must be the message it came from, like 'msg #12'")
    elif kind == "fact":
        if re.match(r"https?://", src):
            if anchor:
                raise MemoryRefused("an anchor applies to a file path, not a URL")
            extra["check"] = "manual"
        else:
            if not anchor:
                raise MemoryRefused("a fact citing a file needs --anchor: a string that must occur in that file")
            if '"' in anchor or "\n" in anchor:
                raise MemoryRefused("an anchor cannot contain a double quote or a newline")
            if not anchor_in_base(repo_reader, repo, src, anchor):
                raise MemoryRefused(f"anchor {anchor!r} not found in {src} on {repo}'s base branch now")
            src = f'{src} anchor: "{anchor}"'
    elif anchor:
        raise MemoryRefused("--anchor is only for facts")
    return src, extra


def _check_text(edir, text, exclude_id=None):
    if not text.strip():
        raise MemoryRefused("empty entry")
    words = len(text.split())
    if words > MAX_WORDS:
        raise MemoryRefused(f"entry is {words} words, the limit is {MAX_WORDS}: split it or cut it")
    secret = zedlib.find_secret(text)
    if secret:
        raise MemoryRefused(f"looks like a secret ({secret}): credentials are never remembered, whatever the kind")
    norm = normalise(text)
    for _, e in all_entries(edir):
        if e.id != exclude_id and normalise(e.body) == norm:
            raise MemoryRefused(f"duplicates {e.id}")


def remember(edir, kind, text, src, *, repo=None, hat=None, tags=(), anchor=None, ttl=None,
             repo_reader=None, today=None):
    """Add an entry; returns it. Refuses: unknown kind, missing src, missing/absent anchor, over 120 words, a secret,
    a duplicate. The date is stamped here. A new entry citing the same src as an existing one with different text
    is accepted and flagged `conflicts: m-N` so the employee merges them."""
    if kind not in KINDS:
        raise MemoryRefused(f"unknown kind {kind!r}: one of {', '.join(KINDS)}")
    if hat and kind not in ("lesson", "work"):
        raise MemoryRefused("--hat applies to lessons (which craft file) and work entries (the hat worn)")
    today = today or _today()
    src_stored, extra = _check_src(kind, src, anchor, repo, repo_reader or git_reader())
    _check_text(edir, text)
    path = entry_file(edir, kind, repo, hat)
    fields = {"tags": ",".join(t.strip() for t in tags if t.strip()), "src": src_stored, **extra}
    if kind == "work" and hat:
        fields["hat"] = hat
    if kind in ("fact", "preference"):
        fields["verified"] = today
    if kind in DEFAULT_TTL:
        fields["ttl"] = f"{ttl or DEFAULT_TTL[kind]}d"
    fields["uses"] = "0"
    clash = [e.id for _, e in all_entries(edir)
             if normalise(e.get("src", "")) == normalise(src_stored) and e.kind == kind]
    if clash:
        fields["conflicts"] = ",".join(clash)
    entry = Entry(next_id(edir), kind, today, fields, text)
    preamble, entries = load(path)
    entries.append(entry)
    save(path, preamble, entries)
    return entry


def refresh(edir, entry_id, *, text=None, src=None, anchor=None, repo_reader=None, today=None):
    """Re-check an entry and re-stamp `verified`. A fact must pass its anchor check again; a `heard` entry upgrades
    to a fact when given the file (src) and anchor that confirm it. Clears stale/unresolved marks."""
    today = today or _today()
    path, entry = find(edir, entry_id)
    repo = path.stem if path.parent.name == "repos" else None
    new_text = text if text is not None else entry.body
    if text is not None:
        _check_text(edir, new_text, exclude_id=entry.id)
    if entry.kind == "heard":
        if not (src and anchor):
            raise MemoryRefused("a heard entry becomes a fact only with the file (--src) and --anchor that confirm it")
        entry.kind = "fact"
        entry.fields.pop("verified", None)
        entry.fields["ttl"] = f"{DEFAULT_TTL['fact']}d"
    if entry.kind == "fact":
        stored, extra = _check_src("fact", src or entry.src_path, anchor or entry.anchor, repo,
                                   repo_reader or git_reader())
        entry.fields["src"] = stored
        entry.fields.pop("check", None)
        entry.fields.update(extra)
    elif src:
        entry.fields["src"] = src.strip()
    entry.body = new_text.strip()
    if entry.kind in ("fact", "preference"):
        entry.fields["verified"] = today
    elif entry.kind == "work":
        entry.date = today  # a refreshed work note starts its ttl again
    for key in ("stale", "unresolved"):
        entry.fields.pop(key, None)
    preamble, entries = load(path)
    save(path, preamble, [entry if e.id == entry.id else e for e in entries])
    return entry


def drop(edir, entry_id, reason, today=None):
    """Move an entry to archive.md with the reason; returns it."""
    if not (reason or "").strip():
        raise MemoryRefused("drop needs --reason")
    path, entry = find(edir, entry_id)
    preamble, entries = load(path)
    save(path, preamble, [e for e in entries if e.id != entry_id])
    archive(edir, entry, reason.strip(), today)
    return entry


# ---------------------------------------------------------------- verify


def verify(edir, repo, *, repo_reader=None, today=None):
    """Check the repo's entries against the base branch, deterministically. Marks a fact whose anchor is gone `stale`
    (and clears the mark when it is back), archives `work` entries past their ttl. Returns
    {'ok': [ids], 'stale': [ids], 'expired': [ids], 'archived': [ids]}. Does not re-stamp `verified`: only `refresh` does."""
    today = today or _today()
    reader = repo_reader or git_reader()
    path = memory_dir(edir) / "repos" / f"{repo}.md"
    preamble, entries = load(path)
    report = {"ok": [], "stale": [], "expired": [], "archived": []}
    keep = []
    for e in entries:
        if e.kind == "work" and is_expired(e, today):
            archive(edir, e, f"work entry past its {e.get('ttl')} ttl", today)
            report["archived"].append(e.id)
            continue
        keep.append(e)
        if e.kind == "fact" and e.anchor and not anchor_in_base(reader, repo, e.src_path, e.anchor):
            e.fields.setdefault("stale", today)
            report["stale"].append(e.id)
        else:
            e.fields.pop("stale", None)
            e.fields.pop("unresolved", None)
            (report["expired"] if is_expired(e, today) else report["ok"]).append(e.id)
    if entries or path.exists():
        save(path, preamble, keep)
    return report


# ---------------------------------------------------------------- pack


def _line(entry, note=""):
    tags = f" ({', '.join(entry.tags)})" if entry.tags else ""
    return f"- [{entry.id}] {entry.kind}{tags}{note}: {' '.join(entry.body.split())}"


def build_pack(edir, name, *, repo, hat, tags=(), memory_words=DEFAULT_MEMORY_WORDS, today=None):
    """The memory handed to one assignment: (text, used_ids, attention_ids). Pure: reads files, writes nothing,
    same inputs give the same bytes. Selection per the design: the hat's lessons and the general ones by `uses`
    (at most 400 words); the repo's fact/preference/heard/work entries whose tags intersect `tags` (all of them when
    no tags are given), newest first until `memory_words`, work capped at 150 words; entries past their ttl only as
    UNVERIFIED, at most 5; stale entries are left out and listed under 'Needs your attention'."""
    today = today or _today()
    tags = {t.strip() for t in tags if t.strip()}
    mem = memory_dir(edir)
    lessons = []
    for f in (mem / "craft" / f"{hat}.md", mem / "craft" / "general.md"):
        lessons += load(f)[1]
    lessons.sort(key=lambda e: (-e.uses, e.id))
    chosen_lessons, used_words = [], 0
    for e in lessons:
        if used_words + e.words > LESSON_PACK_WORDS:
            break
        chosen_lessons.append(e)
        used_words += e.words

    repo_entries = load(mem / "repos" / f"{repo}.md")[1]
    candidates = [e for e in repo_entries if not tags or tags & set(e.tags)]
    stale = [e for e in repo_entries if e.get("stale")]
    live = [e for e in candidates if not e.get("stale")]
    live.sort(key=lambda e: (is_expired(e, today), _neg_date(stamp_date(e)), e.id))
    chosen, budget, work_words, unverified = [], memory_words, 0, 0
    for e in live:
        expired = is_expired(e, today)
        if expired and unverified >= MAX_UNVERIFIED:
            continue
        if e.words > budget:
            break
        if e.kind == "work":
            if work_words + e.words > WORK_PACK_WORDS:
                continue
            work_words += e.words
        budget -= e.words
        unverified += expired
        chosen.append((e, expired))

    out = [f"# Memory pack: {name} · hat {hat} · repo {repo}" + (f" · tags {', '.join(sorted(tags))}" if tags else ""),
           "", "Memory is a lead, not a source: cite what you actually read, and check the code before relying on it.", ""]
    if stale:
        out += ["## Needs your attention",
                "Each of these facts no longer matches the code. In your first plan steps run "
                "`zed-employee refresh <id> ...` (still true, with the new anchor) or `zed-employee drop <id> --reason ...`.", ""]
        out += [_line(e, f" STALE since {e.get('stale')}") for e in stale] + [""]
    out += [f"## Lessons ({hat} and general)"] + ([_line(e) for e in chosen_lessons] or ["(none yet)"]) + [""]
    out += [f"## {repo}"] + ([_line(e, " UNVERIFIED: re-check before relying" if x else "") for e, x in chosen]
                            or ["(nothing remembered for this task yet)"]) + [""]
    used = [e.id for e in chosen_lessons] + [e.id for e, _ in chosen]
    return "\n".join(out), used, [e.id for e in stale]


def _neg_date(d):
    return -int(d.replace("-", ""))


def record_pack(edir, repo, used_ids, attention_ids):
    """After a pack was handed out: `uses` +1 on every entry it carried; entries that were already listed under
    'Needs your attention' UNRESOLVED_LIMIT times are archived instead of listed again. Returns archived ids."""
    path = memory_dir(edir) / "repos" / f"{repo}.md"
    preamble, entries = load(path)
    keep, archived = [], []
    for e in entries:
        if e.id in attention_ids:
            n = int(e.get("unresolved", 0)) + 1
            if n >= UNRESOLVED_LIMIT:
                archive(edir, e, f"stale and unresolved through {n} assignments")
                archived.append(e.id)
                continue
            e.fields["unresolved"] = str(n)
        keep.append(e)
    save(path, preamble, keep)
    for f in all_files(edir):
        pre, ents = load(f)
        hit = [e for e in ents if e.id in used_ids]
        for e in hit:
            e.fields["uses"] = str(e.uses + 1)
        if hit:
            save(f, pre, ents)
    return archived
