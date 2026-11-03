"""zedattention — what needs the user's attention across GitHub, Bitbucket and Jira.

Pure logic plus collectors that take their network access as arguments, so tests pass fakes. Nothing here
posts, approves or comments; every call is a read. Nothing user-specific lives in code: accounts, repos and
exclusions come from the profile's `config/settings.json` → `attention` block.

An item is a dict: source, kind, url, who, ask, since (ISO timestamp), title. Review-queue items also carry
`category` ("reviewed" = the user reviewed it before, "never") and `stale` (True when not requested of the user and
without activity for `max_age_days`: shown in their own section, never dropped).
"""
import base64
import collections
import datetime
import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request

KINDS = ("review-requested", "review-queue", "own-pr-activity", "jira")

NETWORK_TIMEOUT = 30        # seconds per request
CLI_TIMEOUT = 60            # seconds per gh / security call

GH_QUERY = """query($q:String!){search(type:ISSUE,query:$q,first:50){issueCount pageInfo{hasNextPage}
nodes{... on PullRequest{
url number title isDraft updatedAt createdAt author{login __typename} repository{nameWithOwner}
reviewRequests(first:20){nodes{requestedReviewer{... on User{login}}}}
latestOpinionatedReviews(first:30){nodes{author{login} state submittedAt}}
reviews(last:30){nodes{author{login} state submittedAt body}}
comments(last:15){nodes{author{login} createdAt body}}
reviewThreads(last:15){nodes{comments(last:3){nodes{author{login} createdAt body}}}}}}}}"""


class SourceError(Exception):
    """A source could not be read; `needs` lists the missing scopes when that is the cause."""

    def __init__(self, message, needs=None):
        super().__init__(message)
        self.needs = needs or []


def _login(node):
    return ((node or {}).get("author") or {}).get("login") or ""


def _is_bot(node):
    return ((node or {}).get("author") or {}).get("__typename") == "Bot"


def parse_ts(ts):
    """ISO timestamp (`Z`, `+0530`, `+00:00` or naive = UTC) as an aware datetime."""
    value = datetime.datetime.fromisoformat(ts)
    return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)


def _after(ts, since):
    return bool(ts) and parse_ts(ts) >= parse_ts(since)


def _run_cli(args):
    """Run a CLI; a missing binary or a stall becomes SourceError."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=CLI_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SourceError(f"{args[0]} could not run: {type(e).__name__}") from e


def _first_line(text, limit=140):
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return line[:limit]


# ---------------------------------------------------------------- GitHub

def gh_graphql(query, **variables):
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        args += ["-f", f"{k}={v}"]
    r = _run_cli(args)
    if r.returncode != 0:
        raise SourceError(f"gh api graphql failed: {r.stderr.strip()[:200]}")
    try:
        return json.loads(r.stdout)
    except ValueError as e:
        raise SourceError("gh api graphql returned invalid JSON") from e


def gh_login():
    r = _run_cli(["gh", "api", "user", "--jq", ".login"])
    if r.returncode != 0:
        raise SourceError(f"gh api user failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def github_search(graphql, q):
    return graphql(GH_QUERY, q=q)["data"]["search"]


def github_items(cfg, since, graphql=gh_graphql, me=None, notes=None):
    """`notes` (a list) receives a line for every search that has more results than one page shows."""
    me = me or cfg.get("login") or gh_login()
    excluded = set(cfg.get("exclude_authors", []))
    queries = ["is:pr is:open review-requested:@me", "is:pr is:open reviewed-by:@me",
               "is:pr is:open author:@me"]
    queries += [f"is:pr is:open repo:{r}" for r in cfg.get("repos", [])]
    seen, reviewed_urls = {}, set()
    for q in queries:
        found = github_search(graphql, f"{q} sort:updated-desc")
        if found.get("pageInfo", {}).get("hasNextPage") and notes is not None:
            notes.append(f"github: {len(found['nodes'])} of {found.get('issueCount', '?')} shown for `{q}` (most recently updated)")
        for pr in found["nodes"]:
            if pr.get("url"):
                seen.setdefault(pr["url"], pr)
                if "reviewed-by:@me" in q:
                    reviewed_urls.add(pr["url"])
    items = []
    for pr in seen.values():
        author = _login(pr)
        if author == me:
            items += _github_own(pr, me, since, excluded)
        elif (_is_bot(pr) or author in excluded) or (pr["isDraft"] and not cfg.get("include_drafts")):
            continue
        else:
            items += _github_review(pr, me, _cutoff(cfg), pr["url"] in reviewed_urls)
    return items


def _cutoff(cfg):
    """Review-queue PRs (not asking the user directly) without activity since this moment are marked stale."""
    days = cfg.get("max_age_days", 14)
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)


def _stale(updated, requested, cutoff):
    return not requested and parse_ts(updated) < cutoff


def _github_review(pr, me, cutoff, reviewed_by_search):
    """`reviewed_by_search`: the PR came back from `reviewed-by:@me`, which sees reviews older than the fetched window."""
    if any(r["state"] == "APPROVED" for r in pr["latestOpinionatedReviews"]["nodes"]):
        return []
    requested = any(((n.get("requestedReviewer") or {}).get("login") == me) for n in pr["reviewRequests"]["nodes"])
    reviewed = reviewed_by_search or any(_login(n) == me for n in pr["reviews"]["nodes"] + pr["comments"]["nodes"])
    return [{"source": "github", "kind": "review-requested" if requested else "review-queue",
             "category": "reviewed" if reviewed else "never",
             "stale": _stale(pr["updatedAt"], requested, cutoff),
             "url": pr["url"], "who": _login(pr) or "(deleted user)",
             "ask": "review requested" if requested else "open, no approval",
             "since": pr["createdAt"], "updated": pr["updatedAt"], "title": pr["title"]}]


def _github_own(pr, me, since, excluded):
    events = []
    for r in pr["reviews"]["nodes"]:
        if _login(r) not in (me, *excluded) and _after(r.get("submittedAt"), since):
            events.append((r["submittedAt"], _login(r),
                           "changes requested" if r["state"] == "CHANGES_REQUESTED" else f"review {r['state'].lower()}",
                           r.get("body")))
    for c in pr["comments"]["nodes"]:
        if _login(c) not in (me, *excluded) and _after(c.get("createdAt"), since):
            events.append((c["createdAt"], _login(c), "commented", c.get("body")))
    for t in pr["reviewThreads"]["nodes"]:
        for c in t["comments"]["nodes"]:
            if _login(c) not in (me, *excluded) and _after(c.get("createdAt"), since):
                events.append((c["createdAt"], _login(c), "inline comment", c.get("body")))
    if not events:
        return []
    events.sort()
    first = events[0]
    kinds = sorted({e[2] for e in events})
    who = sorted({e[1] for e in events})
    return [{"source": "github", "kind": "own-pr-activity", "url": pr["url"], "who": ", ".join(who),
             "ask": f"{', '.join(kinds)}: {_first_line(events[-1][3])}".rstrip(": "),
             "since": first[0], "updated": events[-1][0], "title": pr["title"]}]


# ---------------------------------------------------------------- Bitbucket

def bb_module(path):
    from importlib.machinery import SourceFileLoader
    from importlib.util import module_from_spec, spec_from_loader
    loader = SourceFileLoader("bb", str(path))
    module = module_from_spec(spec_from_loader("bb", loader))
    loader.exec_module(module)
    return module


Scan = collections.namedtuple("Scan", "me since excluded include_drafts cutoff get")


def bitbucket_items(cfg, since, get, me_uuid=None):
    """`get(path)` returns parsed JSON for a Bitbucket 2.0 API path (raises SourceError on failure)."""
    me = me_uuid or get("/user")["uuid"]
    scan = Scan(me, since, set(cfg.get("exclude_authors", [])), cfg.get("include_drafts"), _cutoff(cfg), get)
    items = []
    for repo in cfg.get("repos", []):
        ws_repo = f"{cfg['workspace']}/{repo}"
        for pr in _bb_pages(get, f"/repositories/{ws_repo}/pullrequests?state=OPEN&pagelen=50"):
            full = get(f"/repositories/{ws_repo}/pullrequests/{pr['id']}")
            item = _bb_item(full, ws_repo, scan)
            if item:
                items.append(item)
    return items


def _bb_pages(get, path):
    while path:
        page = get(path)
        yield from page.get("values", [])
        path = (page.get("next") or "").replace("https://api.bitbucket.org/2.0", "") or None


def _bb_item(pr, ws_repo, scan):
    url = pr["links"]["html"]["href"]
    author = pr["author"]
    parts = pr.get("participants", [])
    me = scan.me
    if author.get("uuid") == me:
        return _bb_own(pr, ws_repo, url, scan)
    if author.get("nickname") in scan.excluded or (pr.get("draft") and not scan.include_drafts):
        return None
    if any(p.get("approved") or p.get("state") == "approved" for p in parts):
        return None
    mine = [p for p in parts if (p.get("user") or {}).get("uuid") == me]
    reviewed = any(p.get("participated_on") or p.get("approved") or p.get("state") for p in mine)
    requested = any(r.get("uuid") == me for r in pr.get("reviewers", []))
    return {"source": "bitbucket", "kind": "review-requested" if requested else "review-queue",
            "category": "reviewed" if reviewed else "never",
            "stale": _stale(pr["updated_on"], requested, scan.cutoff),
            "url": url, "who": author.get("display_name", ""),
            "ask": "review requested" if requested else "open, no approval",
            "since": pr["created_on"], "updated": pr["updated_on"], "title": pr["title"]}


def _bb_own(pr, ws_repo, url, scan):
    me, since, excluded = scan.me, scan.since, scan.excluded
    comments = [c for c in _bb_pages(scan.get, f"/repositories/{ws_repo}/pullrequests/{pr['id']}/comments?sort=-created_on&pagelen=50")
                if (c.get("user") or {}).get("uuid") != me and _after(c.get("created_on"), since)
                and (c.get("user") or {}).get("nickname") not in excluded and not c.get("deleted")]
    changes = [p for p in pr.get("participants", []) if p.get("state") == "changes_requested"
               and _after(p.get("participated_on"), since)]
    if not comments and not changes:
        return None
    events = sorted([c["created_on"] for c in comments] + [p["participated_on"] for p in changes])
    who = sorted({c["user"]["display_name"] for c in comments} | {p["user"]["display_name"] for p in changes})
    ask = "changes requested" if changes else "commented"
    if comments:
        ask += f": {_first_line((comments[0].get('content') or {}).get('raw'))}"
    return {"source": "bitbucket", "kind": "own-pr-activity", "url": url, "who": ", ".join(who), "ask": ask,
            "since": events[0], "updated": events[-1], "title": pr["title"]}


# ---------------------------------------------------------------- Jira (REST, read-only Keychain token)

JIRA_NEEDS = ["read:jira-work", "read:jira-user"]


def keychain_token(service):
    r = _run_cli(["security", "find-generic-password", "-s", service, "-w"])
    if r.returncode != 0:
        raise SourceError(f"no Keychain item '{service}'")
    return r.stdout.strip()


def atlassian_get(email, token, url):
    """Return parsed JSON; HTTP errors become SourceError (a 401 'scope does not match' is a token-scope gap)."""
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode())
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SourceError(f"HTTP {e.code} {url.split('?')[0]}: {e.read().decode(errors='replace')[:120]}") from e
    except (urllib.error.URLError, OSError) as e:
        raise SourceError(f"{url.split('?')[0]} unreachable: {type(e).__name__}") from e
    except ValueError as e:
        raise SourceError(f"{url.split('?')[0]} returned invalid JSON") from e


def _adf_text(node):
    if isinstance(node, dict):
        text = node.get("text", "") if node.get("type") == "text" else ""
        if node.get("type") == "mention":
            text = "@" + (node.get("attrs", {}).get("text") or "").lstrip("@")
        return text + " ".join(_adf_text(c) for c in node.get("content", []))
    return ""


def _adf_mentions(node, account_id):
    if isinstance(node, dict):
        if node.get("type") == "mention" and node.get("attrs", {}).get("id") == account_id:
            return True
        return any(_adf_mentions(c, account_id) for c in node.get("content", []))
    return False


def jira_items(cfg, since, get):
    """Issues assigned to / reported by the user, or commented on by others, since `since`. `get(path)` -> JSON.

    `account_id` and `site` default to what the API says about the token's owner and site."""
    me = cfg.get("account_id") or get("/rest/api/3/myself")["accountId"]
    site = cfg.get("site") or get("/rest/api/3/serverInfo")["baseUrl"].rstrip("/")
    day = (parse_ts(since) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")   # the JQL date is in the account's zone; _after trims
    jql = (f'(assignee = currentUser() OR reporter = currentUser() OR comment ~ "{me}") '
           f'AND updated >= "{day}" ORDER BY updated DESC')
    found = get("/rest/api/3/search/jql?" + urllib.parse.urlencode(
        {"jql": jql, "fields": "summary,assignee,reporter,created", "maxResults": 50}))
    items = []
    for issue in found.get("issues", []):
        key, f = issue["key"], issue["fields"]
        url = f"{site}/browse/{key}"
        comments = get(f"/rest/api/3/issue/{key}/comment?orderBy=-created&maxResults=30").get("comments", [])
        mine = (f.get("assignee") or {}).get("accountId") == me or (f.get("reporter") or {}).get("accountId") == me
        fresh = [c for c in comments if c["author"]["accountId"] != me and _after(c["created"], since)
                 and (mine or _adf_mentions(c["body"], me))]
        if fresh:
            c = fresh[0]            # newest first
            items.append({"source": "jira", "kind": "jira", "url": url, "who": c["author"]["displayName"],
                          "ask": f"commented: {_first_line(_adf_text(c['body']))}", "since": fresh[-1]["created"],
                          "updated": c["created"], "title": f["summary"]})
        elif (f.get("assignee") or {}).get("accountId") == me and _after(f["created"], since):
            items.append({"source": "jira", "kind": "jira", "url": url,
                          "who": (f.get("reporter") or {}).get("displayName", ""), "ask": "created and assigned to you",
                          "since": f["created"], "updated": f["created"], "title": f["summary"]})
    return items


# ---------------------------------------------------------------- run + render

def run(settings, since, sources, fetchers):
    """Collect from each named source; a failing source is reported in `errors`, never fatal to the others."""
    items, errors, notes = [], [], []
    for name in sources:
        cfg = settings.get(name)
        if cfg is None:
            errors.append({"source": name, "message": "no settings block", "needs": []})
            continue
        try:
            got = fetchers[name](cfg, since)
            if isinstance(got, tuple):          # (items, notes)
                got, extra = got
                notes += extra
            items += got
        except SourceError as e:
            errors.append({"source": name, "message": str(e), "needs": e.needs})
        except (KeyError, TypeError) as e:
            errors.append({"source": name, "message": f"unexpected response shape: {e!r}", "needs": []})
        except (OSError, ValueError) as e:
            errors.append({"source": name, "message": f"{type(e).__name__}: {e}"[:200], "needs": []})
    items.sort(key=lambda i: (KINDS.index(i["kind"]), parse_ts(i["since"])))
    return {"since": since, "scanned": list(sources), "items": items, "errors": errors, "notes": notes}


# (title, kind, category, stale)
SECTIONS = [
    ("Review requested of you, no approval — you reviewed before", "review-requested", "reviewed", False),
    ("Review requested of you, no approval — never reviewed by you", "review-requested", "never", False),
    ("Open PRs, no approval — you reviewed before", "review-queue", "reviewed", False),
    ("Open PRs, no approval — never reviewed by you", "review-queue", "never", False),
    ("Stale open PRs (no recent activity), no approval — you reviewed before", "review-queue", "reviewed", True),
    ("Stale open PRs (no recent activity), no approval — never reviewed by you", "review-queue", "never", True),
    ("Your PRs with new review activity", "own-pr-activity", None, False),
    ("Jira", "jira", None, False),
]


def render_text(result):
    out = [f"Needs your attention (since {result['since']})"]
    for title, kind, cat, stale in SECTIONS:
        rows = [i for i in result["items"] if i["kind"] == kind and (cat is None or i.get("category") == cat)
                and bool(i.get("stale")) == stale]
        if rows:
            out += [f"\n{title}"] + [_line(i) for i in rows]
    for e in result["errors"]:
        needs = f" (needs scopes: {', '.join(e['needs'])})" if e["needs"] else ""
        out.append(f"\n! {e['source']}: {e['message']}{needs}")
    out += [f"\n- {n}" for n in result.get("notes", [])]
    if result.get("scanned") and not result["items"] and not result["errors"]:
        out.append("\nNothing.")
    return "\n".join(out)


def _line(i):
    return f"- {i['url']} | {i['who']} | {i['ask']} | since {i['since'][:16]} | {i['title'][:70]}"

