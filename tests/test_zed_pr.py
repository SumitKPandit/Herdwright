import argparse
import contextlib
import io
import unittest

import _load

pr = _load.load("zed-pr")


def make(**kw):
    base = {"forge": "github", "url": "https://github.com/o/r/pull/1", "state": "OPEN", "draft": True,
            "title": "MENT-1 | Add thing", "base": "main", "head": "abc", "author": "me", "conflicts": False,
            "changes_requested": False, "checks": {"passing": 2, "failing": [], "pending": []}, "reviewers": ["a", "b"]}
    base.update(kw)
    return base


class PrTests(unittest.TestCase):
    def test_parse_url(self):
        self.assertEqual(pr.parse_url("https://github.com/o/r/pull/7/files"), ("github", "o/r", 7))
        self.assertEqual(pr.parse_url("https://bitbucket.org/ws/s/pull-requests/3"), ("bitbucket", "ws/s", 3))
        with self.assertRaises(pr.PrLookupError):
            pr.parse_url("https://example.com/x")

    def test_github_check_summary(self):
        s = pr.summarize_github_checks([
            {"__typename": "CheckRun", "name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"},
            {"__typename": "CheckRun", "name": "e2e", "status": "IN_PROGRESS", "conclusion": ""},
            {"__typename": "CheckRun", "name": "skipme", "status": "COMPLETED", "conclusion": "SKIPPED"},
            {"__typename": "StatusContext", "context": "ci/x", "state": "ERROR"},
            {"__typename": "StatusContext", "context": "ci/y", "state": "PENDING"}])
        self.assertEqual(s, {"passing": 2, "failing": ["ci/x", "lint"], "pending": ["ci/y", "e2e"]})

    def test_verdict_order(self):
        self.assertEqual(pr.verdict(make(state="MERGED")), "merged")
        self.assertEqual(pr.verdict(make(state="CLOSED")), "closed")
        self.assertEqual(pr.verdict(make(checks={"passing": 1, "failing": ["x"], "pending": ["y"]})), "ci-failing")
        self.assertEqual(pr.verdict(make(conflicts=True)), "conflicts")
        self.assertEqual(pr.verdict(make(changes_requested=True)), "changes-requested")
        self.assertEqual(pr.verdict(make(checks={"passing": 1, "failing": [], "pending": ["y"]})), "ci-pending")
        self.assertEqual(pr.verdict(make(checks={"passing": 0, "failing": [], "pending": []})), "ci-none")
        self.assertEqual(pr.verdict(make()), "green")

    def run_wait(self, seq, timeout=100, interval=10):
        calls, naps = iter(seq), []
        clock = [0.0]

        def fetcher(_):
            r = next(calls)
            if isinstance(r, Exception):
                raise r
            return r

        def sleep(n):
            naps.append(n)
            clock[0] += n
        a = argparse.Namespace(url="u", timeout=timeout, interval=interval, json=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = pr.cmd_wait(a, fetcher, sleep, lambda: clock[0])
        return code, naps, out.getvalue()

    def test_wait_until_green(self):
        pending = make(checks={"passing": 1, "failing": [], "pending": ["e2e"]})
        code, naps, out = self.run_wait([pending, pending, make()])
        self.assertEqual((code, naps), (0, [10, 10]))
        self.assertIn("green", out)

    def test_wait_failure_returns_immediately(self):
        code, naps, out = self.run_wait([make(checks={"passing": 1, "failing": ["lint"], "pending": []})])
        self.assertEqual((code, naps), (1, []))
        self.assertIn("failing: lint", out)

    def test_wait_times_out_pending(self):
        pending = make(checks={"passing": 1, "failing": [], "pending": ["e2e"]})
        code, naps, _ = self.run_wait([pending] * 20, timeout=25, interval=10)
        self.assertEqual((code, naps), (3, [10, 10]))

    def test_wait_survives_transient_errors_but_not_three(self):
        err = pr.PrLookupError("504")
        code, _, _ = self.run_wait([err, make()])
        self.assertEqual(code, 0)
        with self.assertRaises(pr.PrLookupError):
            self.run_wait([err, err, err])

    def test_ready_problems(self):
        self.assertEqual(pr.ready_problems(make(), pr.TITLE_PATTERN, "main", ["a", "b"]), [])
        probs = pr.ready_problems(make(title="fix stuff", draft=False, base="dev", conflicts=True,
                                       checks={"passing": 1, "failing": ["lint"], "pending": []}),
                                  pr.TITLE_PATTERN, "main", ["a", "c", "me"])
        text = "\n".join(probs)
        for needle in ("already out of draft", "CI failing: lint", "title does not match", "base is dev", "reviewers not requested: c"):
            self.assertIn(needle, text)
        self.assertNotIn("me", probs[-1].split(": ")[1].split(", "))  # the author is never a missing reviewer

    def test_people_handles(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "people.md"
            p.write_text("## PR reviewers\nGitHub handles to request on every PR once ready:\n`dgavey`, `Madhu-864`\n")
            self.assertEqual(pr.people_github_handles(p), ["dgavey", "madhu-864"])
            self.assertEqual(pr.people_github_handles(Path(d) / "missing.md"), [])


if __name__ == "__main__":
    unittest.main()
