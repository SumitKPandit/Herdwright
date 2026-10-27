"""docs/runtimes/*.md must give every probed adapter cell a dated verdict, and a fallback unless it is `yes`."""
import re
import unittest
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs" / "runtimes"
RUNTIMES = ("claude", "copilot")
CELLS = ("deny", "identity", "stop hook", "skills", "subagent cap", "prompt to a *working* session",
         "doorbell")
VERDICTS = {"yes", "partial", "no"}


def rows(runtime):
    """{cell label: [verdict, verified, evidence, fallback]} from the markdown table."""
    out = {}
    for line in (DOCS / f"{runtime}.md").read_text().splitlines():
        if not line.startswith("|") or line.startswith("|---") or line.startswith("| Cell"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        out[cells[0].lower()] = cells[1:]
    return out


class RuntimeDocs(unittest.TestCase):
    def test_every_cell_has_a_dated_verdict(self):
        for rt in RUNTIMES:
            table = rows(rt)
            for cell in CELLS:
                matches = [v for k, v in table.items() if k.startswith(cell)]
                self.assertEqual(len(matches), 1, f"{rt}: cell '{cell}' missing or duplicated")
                verdict, verified, evidence, fallback = matches[0]
                self.assertIn(verdict, VERDICTS, f"{rt}/{cell}")
                self.assertRegex(verified, r"^\d{4}-\d{2}-\d{2}$", f"{rt}/{cell}")
                self.assertTrue(evidence, f"{rt}/{cell}: no evidence")
                self.assertTrue(fallback, f"{rt}/{cell}: no fallback column")
                if verdict != "yes":
                    self.assertNotIn(fallback.lower(), {"none", "none needed"}, f"{rt}/{cell}: needs a real fallback")

    def test_pinned_version_stated(self):
        for rt in RUNTIMES:
            self.assertRegex((DOCS / f"{rt}.md").read_text(), r"Verified \d{4}-\d{2}-\d{2} on .*\d+\.\d+\.\d+", rt)


if __name__ == "__main__":
    unittest.main()
