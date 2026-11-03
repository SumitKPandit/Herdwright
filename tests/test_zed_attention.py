import io
import json
import unittest

import _load

za = __import__("zedattention")
cli = _load.load("zed-attention")

ME = "me"
SINCE = "2026-10-26T00:00:00Z"
FRESH = "2999-01-01T00:00:00Z"   # far future: never older than the stale cutoff


def gh_pr(url, author="alice", reviews=(), comments=(), requested=(), draft=False, bot=False, updated=FRESH,
          threads=(), opinionated=None):
    def rv(items):
        return {"nodes": [{"author": {"login": a}, "state": s, "submittedAt": t, "body": b}
                          for a, s, t, b in items]}
    return {"url": url, "number": 1, "title": "T", "isDraft": draft, "updatedAt": updated, "createdAt": "2026-10-20T00:00:00Z",
            "author": {"login": author, "__typename": "Bot" if bot else "User"},
            "repository": {"nameWithOwner": "o/r"},
            "reviewRequests": {"nodes": [{"requestedReviewer": {"login": r}} for r in requested]},
            "latestOpinionatedReviews": {"nodes": [{"author": {"login": a}, "state": s, "submittedAt": t}
                                                   for a, s, t, _ in (reviews if opinionated is None else opinionated)]},
            "reviews": rv(reviews),
            "comments": {"nodes": [{"author": {"login": a}, "createdAt": t, "body": b} for a, t, b in comments]},
            "reviewThreads": {"nodes": [{"comments": {"nodes": [{"author": {"login": a}, "createdAt": t, "body": b}
                                                                for a, t, b in threads]}}]}}


def fake_graphql(by_query, more=None):
    def graphql(_query, q):
        base = q.replace(" sort:updated-desc", "")
        assert q.endswith(" sort:updated-desc")
        nodes = by_query.get(base, [])
        return {"data": {"search": {"nodes": nodes, "issueCount": more or len(nodes),
                                    "pageInfo": {"hasNextPage": bool(more)}}}}
    return graphql


class GithubTests(unittest.TestCase):
    cfg = {"login": ME, "repos": ["o/r"], "exclude_authors": ["skip"]}

    def collect(self, prs, cfg=None, more=None, notes=None):
        return za.github_items(cfg or self.cfg, SINCE, fake_graphql({"is:pr is:open repo:o/r": prs}, more), me=ME,
                               notes=notes)

    def test_approved_pr_is_dropped(self):
        pr = gh_pr("u1", reviews=[("bob", "APPROVED", "t", "")])
        self.assertEqual(self.collect([pr]), [])

    def test_never_vs_reviewed_by_review_or_comment(self):
        never = gh_pr("u1")
        by_review = gh_pr("u2", reviews=[(ME, "COMMENTED", "t", "")])
        by_comment = gh_pr("u3", comments=[(ME, "t", "hi")])
        got = {i["url"]: i["category"] for i in self.collect([never, by_review, by_comment])}
        self.assertEqual(got, {"u1": "never", "u2": "reviewed", "u3": "reviewed"})

    def test_requested_kind_and_bots_drafts_excluded(self):
        prs = [gh_pr("req", requested=[ME]), gh_pr("bot", bot=True), gh_pr("skip", author="skip"),
               gh_pr("draft", draft=True)]
        self.assertEqual([(i["url"], i["kind"]) for i in self.collect(prs)], [("req", "review-requested")])

    def test_approved_then_commented_stays_approved(self):
        pr = gh_pr("u1", reviews=[("bob", "APPROVED", "t1", ""), ("bob", "COMMENTED", "t2", "")],
                   opinionated=[("bob", "APPROVED", "t1", "")])
        self.assertEqual(self.collect([pr]), [])

    def test_unapproved_when_latest_opinion_is_changes_requested_and_old_approval_is_outside_window(self):
        changed = gh_pr("u1", reviews=[("bob", "APPROVED", "t1", ""), ("bob", "CHANGES_REQUESTED", "t2", "")],
                        opinionated=[("bob", "CHANGES_REQUESTED", "t2", "")])
        self.assertEqual([i["url"] for i in self.collect([changed])], ["u1"])
        old_approval = gh_pr("u2", reviews=[("amy", "COMMENTED", "t", "")], opinionated=[("bob", "APPROVED", "t0", "")])
        self.assertEqual(self.collect([old_approval]), [])

    def test_reviewed_comes_from_the_reviewed_by_search_when_the_review_is_outside_the_fetched_window(self):
        pr = gh_pr("u1", reviews=[("amy", "COMMENTED", "t", "")])
        graphql = fake_graphql({"is:pr is:open repo:o/r": [pr, gh_pr("u2")],
                                "is:pr is:open reviewed-by:@me": [gh_pr("u1", reviews=[("amy", "COMMENTED", "t", "")])]})
        got = {i["url"]: i["category"] for i in za.github_items(self.cfg, SINCE, graphql, me=ME)}
        self.assertEqual(got, {"u1": "reviewed", "u2": "never"})

    def test_stale_queue_pr_is_marked_not_dropped_requested_is_not_stale(self):
        old = "2020-01-01T00:00:00+05:30"
        (stale,) = self.collect([gh_pr("old", updated=old)])
        self.assertEqual((stale["url"], stale["stale"]), ("old", True))
        (asked,) = self.collect([gh_pr("old", updated=old, requested=[ME])])
        self.assertFalse(asked["stale"])
        (fresh,) = self.collect([gh_pr("new")])
        self.assertFalse(fresh["stale"])

    def test_more_than_one_page_is_noted(self):
        notes = []
        self.collect([gh_pr("u1")], more=73, notes=notes)
        self.assertIn("github: 1 of 73 shown for `is:pr is:open repo:o/r`", notes[-1])

    def test_own_pr_activity_since(self):
        pr = gh_pr("mine", author=ME, reviews=[("bob", "CHANGES_REQUESTED", "2026-10-27T01:00:00Z", "fix x"),
                                              ("amy", "COMMENTED", "2026-10-20T00:00:00Z", "old")],
                   comments=[(ME, "2026-10-27T02:00:00Z", "mine")])
        (item,) = self.collect([pr])
        self.assertEqual((item["kind"], item["who"]), ("own-pr-activity", "bob"))
        self.assertIn("changes requested", item["ask"])
        self.assertEqual(self.collect([gh_pr("quiet", author=ME)]), [])

    def test_own_pr_activity_compares_instants_across_offsets(self):
        # 05:00+05:30 is 23:30Z the day before SINCE's day boundary
        before = gh_pr("a", author=ME, comments=[("bob", "2026-10-26T05:00:00+05:30", "x")])
        after = gh_pr("b", author=ME, comments=[("bob", "2026-10-26T05:30:00+05:30", "x")])
        self.assertEqual([i["url"] for i in self.collect([before, after])], ["b"])


def bb_pr(id_, author="{a}", participants=(), reviewers=(), draft=False, updated="2999-01-01T00:00:00.123456+00:00"):
    return {"id": id_, "title": "T", "draft": draft, "created_on": "2026-10-01T00:00:00", "updated_on": updated,
            "links": {"html": {"href": f"https://bitbucket.org/w/r/pull-requests/{id_}"}},
            "author": {"uuid": author, "display_name": "Author", "nickname": "auth"},
            "participants": [{"user": {"uuid": u}, "approved": ap, "state": st, "participated_on": on}
                             for u, ap, st, on in participants],
            "reviewers": [{"uuid": r} for r in reviewers]}


def bb_get(prs, comments=None):
    def get(path):
        if path == "/user":
            return {"uuid": "{me}"}
        if "/comments" in path:
            return {"values": (comments or {}).get(int(path.split("pullrequests/")[1].split("/")[0]), [])}
        if "pullrequests?" in path:
            return {"values": [{"id": p["id"]} for p in prs]}
        return next(p for p in prs if path.endswith(f"/{p['id']}"))
    return get


class BitbucketTests(unittest.TestCase):
    cfg = {"workspace": "w", "repos": ["r"]}

    def test_approval_and_reviewed_split(self):
        prs = [bb_pr(1, participants=[("{x}", True, "approved", "t")]),
               bb_pr(2, participants=[("{me}", False, None, "2026-10-02T00:00:00")]),
               bb_pr(3, reviewers=["{me}"]),
               bb_pr(4)]
        got = {i["url"].rsplit("/", 1)[1]: (i["kind"], i["category"])
               for i in za.bitbucket_items(self.cfg, SINCE, bb_get(prs))}
        self.assertEqual(got, {"2": ("review-queue", "reviewed"), "3": ("review-requested", "never"),
                               "4": ("review-queue", "never")})

    def test_stale_pr_is_marked_not_dropped(self):
        prs = [bb_pr(1, updated="2020-01-01T00:00:00"), bb_pr(2, updated="2020-01-01T00:00:00", reviewers=["{me}"]),
               bb_pr(3)]
        got = {i["url"].rsplit("/", 1)[1]: i["stale"] for i in za.bitbucket_items(self.cfg, SINCE, bb_get(prs))}
        self.assertEqual(got, {"1": True, "2": False, "3": False})

    def test_draft_pr_is_skipped_unless_included(self):
        prs = [bb_pr(1, draft=True), bb_pr(2)]
        self.assertEqual([i["url"][-1] for i in za.bitbucket_items(self.cfg, SINCE, bb_get(prs))], ["2"])
        cfg = {**self.cfg, "include_drafts": True}
        self.assertEqual(len(za.bitbucket_items(cfg, SINCE, bb_get(prs))), 2)

    def test_participant_state_approved_without_approved_flag_drops_the_pr(self):
        prs = [bb_pr(1, participants=[("{x}", False, "approved", "t")])]
        self.assertEqual(za.bitbucket_items(self.cfg, SINCE, bb_get(prs)), [])

    def test_deleted_comments_are_ignored(self):
        prs = [bb_pr(9, author="{me}")]
        comments = {9: [{"user": {"uuid": "{o}", "display_name": "Ola"}, "created_on": "2026-10-27T00:00:00",
                         "deleted": True, "content": {"raw": "gone"}}]}
        self.assertEqual(za.bitbucket_items(self.cfg, SINCE, bb_get(prs, comments)), [])

    def test_pull_request_list_follows_next_pages(self):
        base = "https://api.bitbucket.org/2.0"
        pages = {"/repositories/w/r/pullrequests?state=OPEN&pagelen=50":
                 {"values": [{"id": 1}], "next": base + "/page2"},
                 "/page2": {"values": [{"id": 2}]}}
        prs = {1: bb_pr(1), 2: bb_pr(2)}

        def get(path):
            if path == "/user":
                return {"uuid": "{me}"}
            if path in pages:
                return pages[path]
            return prs[int(path.rsplit("/", 1)[1])]
        self.assertEqual(sorted(i["url"][-1] for i in za.bitbucket_items(self.cfg, SINCE, get)), ["1", "2"])

    def test_own_pr_ignores_excluded_authors(self):
        prs = [bb_pr(9, author="{me}")]
        comments = {9: [{"user": {"uuid": "{b}", "display_name": "Bot", "nickname": "skip"},
                         "created_on": "2026-10-27T00:00:00", "content": {"raw": "auto"}}]}
        cfg = {**self.cfg, "exclude_authors": ["skip"]}
        self.assertEqual(za.bitbucket_items(cfg, SINCE, bb_get(prs, comments)), [])

    def test_own_pr_comments_since(self):
        prs = [bb_pr(9, author="{me}")]
        comments = {9: [{"user": {"uuid": "{o}", "display_name": "Ola"}, "created_on": "2026-10-27T00:00:00", "content": {"raw": "please fix"}},
                        {"user": {"uuid": "{me}", "display_name": "Me"}, "created_on": "2026-10-27T01:00:00", "content": {"raw": "ok"}}]}
        (item,) = za.bitbucket_items(self.cfg, SINCE, bb_get(prs, comments))
        self.assertEqual((item["kind"], item["who"], item["ask"]), ("own-pr-activity", "Ola", "commented: please fix"))


def adf(text, mention=None):
    content = [{"type": "text", "text": text}]
    if mention:
        content.insert(0, {"type": "mention", "attrs": {"id": mention, "text": "@Me"}})
    return {"type": "doc", "content": [{"type": "paragraph", "content": content}]}


class AtlassianTests(unittest.TestCase):
    def test_jira_mentions_and_assignment(self):
        issues = {"issues": [
            {"key": "A-1", "fields": {"summary": "s1", "assignee": {"accountId": "acc"}, "reporter": {"accountId": "z"},
                                       "created": "2026-10-01T00:00:00Z"}},
            {"key": "A-2", "fields": {"summary": "s2", "assignee": {"accountId": "z"}, "reporter": {"accountId": "z"},
                                       "created": "2026-10-01T00:00:00Z"}},
            {"key": "A-3", "fields": {"summary": "s3", "assignee": {"accountId": "acc"}, "reporter": {"accountId": "boss", "displayName": "Boss"},
                                       "created": "2026-10-27T00:00:00Z"}}]}
        comments = {"A-1": [{"author": {"accountId": "z", "displayName": "Zoe"}, "created": "2026-10-27T00:00:00Z", "body": adf("ping")}],
                    "A-2": [{"author": {"accountId": "z", "displayName": "Zoe"}, "created": "2026-10-27T00:00:00Z", "body": adf("hi", "acc")},
                            {"author": {"accountId": "z", "displayName": "Zoe"}, "created": "2026-10-27T01:00:00Z", "body": adf("nobody")}],
                    "A-3": []}

        def get(path):
            if "search/jql" in path:
                return issues
            return {"comments": comments[path.split("/issue/")[1].split("/")[0]]}
        cfg = {"account_id": "acc", "site": "https://x"}
        got = {i["url"].rsplit("/", 1)[1]: i["ask"] for i in za.jira_items(cfg, SINCE, get)}
        self.assertEqual(got, {"A-1": "commented: ping", "A-2": "commented: @Me hi", "A-3": "created and assigned to you"})

    def test_jira_since_updated_order_and_offsets(self):
        issue = {"key": "A-1", "fields": {"summary": "s", "assignee": {"accountId": "acc"}, "reporter": {"accountId": "z"},
                                          "created": "2026-10-01T00:00:00Z"}}
        zed = {"accountId": "z", "displayName": "Zoe"}
        comments = [{"author": zed, "created": "2026-10-27T10:00:00.000+0530", "body": adf("newest")},      # 04:30Z
                    {"author": zed, "created": "2026-10-26T05:30:00.000+0530", "body": adf("oldest")},      # 00:00Z, kept
                    {"author": zed, "created": "2026-10-26T05:00:00.000+0530", "body": adf("too old")}]      # 23:30Z day before

        def get(path):
            return {"issues": [issue]} if "search/jql" in path else {"comments": comments}
        (item,) = za.jira_items({"account_id": "acc", "site": "https://x"}, SINCE, get)
        self.assertEqual((item["since"], item["updated"]), ("2026-10-26T05:30:00.000+0530", "2026-10-27T10:00:00.000+0530"))

    def test_jira_needs_mine_or_a_mention_and_ignores_own_comments(self):
        def issue(key, assignee, reporter="z"):
            return {"key": key, "fields": {"summary": "s", "assignee": {"accountId": assignee},
                                           "reporter": {"accountId": reporter}, "created": "2026-10-01T00:00:00Z"}}
        zoe = {"accountId": "z", "displayName": "Zoe"}
        me = {"accountId": "acc", "displayName": "Me"}
        comments = {"A-1": [{"author": zoe, "created": "2026-10-27T00:00:00Z", "body": adf("not for me")}],
                    "A-2": [{"author": me, "created": "2026-10-27T00:00:00Z", "body": adf("my own note")}],
                    "A-3": [{"author": zoe, "created": "2026-10-27T00:00:00Z", "body": adf("hello")}]}
        issues = {"issues": [issue("A-1", "z"), issue("A-2", "acc"), issue("A-3", "acc")]}

        def get(path):
            return issues if "search/jql" in path else {"comments": comments[path.split("/issue/")[1].split("/")[0]]}
        got = [i["url"].rsplit("/", 1)[1] for i in za.jira_items({"account_id": "acc", "site": "https://x"}, SINCE, get)]
        self.assertEqual(got, ["A-3"])

    def test_jira_search_window_starts_a_day_before_since(self):
        seen = []

        def get(path):
            seen.append(path)
            return {"issues": []}
        za.jira_items({"account_id": "acc", "site": "https://x"}, "2026-10-26T00:30:00Z", get)
        self.assertIn("2026-10-25", seen[0])

    def test_jira_account_and_site_come_from_the_token(self):
        calls = []

        def get(path):
            calls.append(path)
            if path.endswith("/myself"):
                return {"accountId": "acc"}
            if path.endswith("/serverInfo"):
                return {"baseUrl": "https://site.example/"}
            if "search/jql" in path:
                return {"issues": [{"key": "A-9", "fields": {"summary": "s", "assignee": {"accountId": "acc"},
                                                              "reporter": {"accountId": "z", "displayName": "Zed"},
                                                              "created": "2026-10-27T00:00:00Z"}}]}
            return {"comments": []}
        (item,) = za.jira_items({}, SINCE, get)
        self.assertEqual((item["url"], item["ask"]), ("https://site.example/browse/A-9", "created and assigned to you"))
        self.assertTrue(all(c.startswith("/rest/api/3/") for c in calls))


class RunAndCliTests(unittest.TestCase):
    def test_failing_source_is_reported_not_fatal(self):
        def boom(cfg, since):
            raise za.SourceError("HTTP 401 scope does not match", za.JIRA_NEEDS)
        ok = {"source": "github", "kind": "review-queue", "category": "never", "url": "u", "who": "a", "ask": "x",
              "since": "2026-10-27T00:00:00Z", "title": "t"}
        res = za.run({"github": {}, "jira": {}}, SINCE, ["github", "jira", "bitbucket"],
                     {"github": lambda c, s: [ok], "jira": boom})
        self.assertEqual(len(res["items"]), 1)
        self.assertEqual({e["source"] for e in res["errors"]}, {"jira", "bitbucket"})
        self.assertEqual(res["errors"][0]["needs"], za.JIRA_NEEDS)

    def test_any_failure_kind_is_reported_per_source(self):
        import urllib.error

        def raising(exc):
            def f(cfg, since):
                raise exc
            return f
        ok = lambda c, s: []
        for exc in (urllib.error.URLError("dns"), TimeoutError("slow"), json.JSONDecodeError("x", "", 0),
                    FileNotFoundError("gh")):
            res = za.run({"github": {}, "jira": {}}, SINCE, ["github", "jira"], {"github": raising(exc), "jira": ok})
            self.assertEqual([e["source"] for e in res["errors"]], ["github"], exc)

    def test_cli_errors_map_to_source_error(self):
        import subprocess
        from unittest import mock
        with mock.patch.object(za.subprocess, "run", side_effect=FileNotFoundError("gh")):
            with self.assertRaises(za.SourceError):
                za.gh_graphql("q")
        with mock.patch.object(za.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 60)):
            with self.assertRaises(za.SourceError):
                za.gh_login()
        bad = mock.Mock(returncode=0, stdout="not json", stderr="")
        with mock.patch.object(za.subprocess, "run", return_value=bad):
            with self.assertRaises(za.SourceError):
                za.gh_graphql("q")

    def test_atlassian_get_maps_network_and_json_errors(self):
        import urllib.error
        from unittest import mock
        with mock.patch.object(za.urllib.request, "urlopen", side_effect=urllib.error.URLError("dns")) as up:
            with self.assertRaises(za.SourceError):
                za.atlassian_get("e", "t", "https://x/y")
            self.assertEqual(up.call_args.kwargs["timeout"], za.NETWORK_TIMEOUT)
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = b"<html>"
        with mock.patch.object(za.urllib.request, "urlopen", return_value=resp):
            with self.assertRaises(za.SourceError):
                za.atlassian_get("e", "t", "https://x/y")

    def test_jira_getter_names_scopes_on_401_and_missing_service(self):
        from unittest import mock
        err = za.SourceError("HTTP 401 https://x: scope does not match")
        with mock.patch.object(za, "keychain_token", return_value="t"), \
                mock.patch.object(za, "atlassian_get", side_effect=err):
            with self.assertRaises(za.SourceError) as cm:
                cli.jira_getter({"keychain_service": "k", "keychain_account": "a", "api_base": "b"})("/p")
        self.assertEqual(cm.exception.needs, za.JIRA_NEEDS)
        with self.assertRaises(za.SourceError) as cm:
            cli.jira_getter({})
        self.assertIn("keychain_service", str(cm.exception))

    def test_bitbucket_getter_maps_systemexit(self):
        from unittest import mock
        bb = mock.Mock()
        bb.call.side_effect = SystemExit(1)
        with mock.patch.object(za, "bb_module", return_value=bb):
            with self.assertRaises(za.SourceError):
                cli.bitbucket_getter()("/user")

    def test_since_sorting_and_normalizing_use_instants(self):
        self.assertEqual(cli.normalize_since("2026-10-27T05:00:00+0530"), "2026-10-26T23:30:00Z")
        self.assertEqual(cli.normalize_since("2026-10-27"), "2026-10-27T00:00:00Z")
        with self.assertRaises(ValueError):
            cli.normalize_since("yesterday")
        self.assertEqual(cli.main(["--since", "yesterday"], {"github": {}}, {}, io.StringIO()), 2)
        mk = lambda u, t: {"source": "g", "kind": "jira", "url": u, "who": "", "ask": "", "since": t, "title": ""}
        res = za.run({"jira": {}}, SINCE, ["jira"], {"jira": lambda c, s: [
            mk("late", "2026-10-26T23:00:00Z"), mk("early", "2026-10-27T01:00:00+0530")]})
        self.assertEqual([i["url"] for i in res["items"]], ["early", "late"])

    def test_render_has_stale_section_and_confluence_only_is_not_nothing(self):
        item = {"source": "github", "kind": "review-queue", "category": "never", "stale": True, "url": "https://x/1",
                "who": "a", "ask": "open, no approval", "since": "2026-10-01T00:00:00Z", "title": "T"}
        text = za.render_text({"since": SINCE, "scanned": ["github"], "items": [item], "errors": [], "notes": []})
        self.assertIn("Stale open PRs", text)
        self.assertNotIn("Nothing", text)
        out = io.StringIO()
        cli.main(["--source", "confluence"], {"github": {}}, {}, out)
        self.assertNotIn("Nothing", out.getvalue())
        out = io.StringIO()
        cli.main(["--source", "github"], {"github": {}}, {"github": lambda c, s: []}, out)
        self.assertIn("Nothing", out.getvalue())

    def test_cli_json_text_and_exit_codes(self):
        item = {"source": "github", "kind": "review-queue", "category": "never", "url": "https://x/1", "who": "a",
                "ask": "open, no approval", "since": "2026-10-27T00:00:00Z", "title": "T"}
        fetchers = {"github": lambda c, s: [item]}
        out = io.StringIO()
        self.assertEqual(cli.main(["--json", "--since", "2026-10-26", "--source", "github"], {"github": {}}, fetchers, out), 0)
        self.assertEqual(json.loads(out.getvalue())["items"][0]["url"], "https://x/1")
        out = io.StringIO()
        cli.main(["--source", "github"], {"github": {}}, fetchers, out)
        self.assertIn("never reviewed by you", out.getvalue())
        self.assertEqual(cli.main(["--source", "nope"], {"github": {}}, fetchers, io.StringIO()), 2)
        self.assertEqual(cli.main([], {}, fetchers, io.StringIO()), 2)
        self.assertEqual(cli.main(["--source", "jira"], {"github": {}}, fetchers, io.StringIO()), 1)

    def test_confluence_is_a_note_not_an_error(self):
        out = io.StringIO()
        self.assertEqual(cli.main(["--source", "confluence"], {"github": {}}, {}, out), 0)
        self.assertIn("pass --confluence-json", out.getvalue())
        out = io.StringIO()
        cli.main(["--json", "--source", "github,confluence"], {"github": {}}, {"github": lambda c, s: []}, out)
        res = json.loads(out.getvalue())
        self.assertEqual((res["errors"], len(res["notes"])), ([], 1))

    def feed_file(self, rows):
        import tempfile, pathlib
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = pathlib.Path(d.name) / "feed.json"
        path.write_text(rows if isinstance(rows, str) else json.dumps(rows))
        return str(path)

    def test_confluence_feed_keeps_fresh_unresolved_rows_and_renders_a_section(self):
        row = {"url": "https://c/1", "title": "Page", "who": "Ann", "ask": "mentioned you: can you check?\nmore",
               "updated": "2026-10-27T10:00:00Z"}
        feed = self.feed_file([row, {**row, "url": "https://c/old", "updated": "2026-10-01T00:00:00Z"},
                               {**row, "url": "https://c/done", "resolved": True}])
        out = io.StringIO()
        self.assertEqual(cli.main(["--confluence-json", feed, "--since", "2026-10-26", "--source", "confluence"],
                                  {"github": {}}, {}, out), 0)
        self.assertIn("Confluence\n- https://c/1 | Ann | mentioned you: can you check? | since 2026-10-27T10:00", out.getvalue())
        self.assertNotIn("c/old", out.getvalue())
        self.assertNotIn("c/done", out.getvalue())

    def test_confluence_feed_problems_are_errors_and_do_not_block_other_sources(self):
        item = {"source": "github", "kind": "review-queue", "category": "never", "url": "https://x/1", "who": "a",
                "ask": "open, no approval", "since": "2026-10-27T00:00:00Z", "title": "T"}
        for bad in ("not json", [{"url": "u"}], {"a": 1}, [{"url": "u", "title": "t", "who": "w", "ask": "a", "updated": "yesterday"}]):
            out = io.StringIO()
            code = cli.main(["--json", "--since", "2026-10-26", "--source", "github,confluence", "--confluence-json",
                             self.feed_file(bad)], {"github": {}}, {"github": lambda c, s: [item]}, out)
            res = json.loads(out.getvalue())
            self.assertEqual((code, [e["source"] for e in res["errors"]], len(res["items"])), (1, ["confluence"], 1), bad)
        out = io.StringIO()
        self.assertEqual(cli.main(["--source", "confluence", "--confluence-json", "/nonexistent/feed.json"], {"github": {}}, {}, out), 1)
        self.assertIn("! confluence: confluence feed /nonexistent/feed.json unreadable", out.getvalue())

    def test_confluence_feed_from_stdin(self):
        rows = json.dumps([{"url": "https://c/2", "title": "P", "who": "Bo", "ask": "commented", "updated": "2026-10-27T10:00:00Z"}])
        got = cli.confluence_fetcher("-", io.StringIO(rows))({}, SINCE)
        self.assertEqual([i["url"] for i in got], ["https://c/2"])

    def test_jira_settings_merge_top_level_block(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "config").mkdir()
            (pathlib.Path(d) / "config" / "settings.json").write_text(json.dumps(
                {"jira": {"api_base": "https://a/b", "keychain_service": "k"}, "attention": {"github": {}, "jira": {"site": "s"}}}))
            got = cli.load_settings(pathlib.Path(d))
        self.assertEqual(got["jira"], {"api_base": "https://a/b", "keychain_service": "k", "site": "s"})


if __name__ == "__main__":
    unittest.main()
