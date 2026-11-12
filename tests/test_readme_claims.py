"""Every statement in README.md is tied to what backs it.

The README's source carries one HTML comment per block (a paragraph, a list item, a table), inside it or right after it:

    <!-- test: test_file.py::Class.test_name; test_other.py::Class.test_name -->   automated tests in the default run
    <!-- smoke: smoke_opencode.py::Class.test_name -->                              the real-OpenCode smoke run
    <!-- note: why -->      not a tested mechanism: an audience, an observation, a stated limit
    <!-- steps -->          something the reader does, followed by hand before a release

A block without a comment fails, and so does a comment that names a test that does not exist. A class that
`per_runtime` turns into one class per runtime is named by its base name (`Watch` for `WatchOnClaude`). That the named
tests pass is the test run's own business: this file checks that each one is there to be run."""
import ast
import re
import unittest
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
TESTS = HOME / "tests"
MARKER = re.compile(r"<!--\s*(test|smoke|note|steps)\b:?\s*(.*?)\s*-->", re.S)
ITEM = re.compile(r"(- |\d+\. )")
KINDS = {"test": "test_", "smoke": "smoke_"}  # the file name each kind of reference starts with


def blocks(text):
    """The README's blocks as (first line number, text): paragraphs, top-level list items with their continuation
    lines, and tables. Headings are not blocks. A paragraph that is only comments belongs to the block before it."""
    found, current = [], None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            current = None
            continue
        starts_item = ITEM.match(line)
        only_comments = not MARKER.sub("", line).strip()
        if current is None and only_comments and found and not found[-1][2]:
            found[-1][1].append(line)  # a comment paragraph: it backs the block above
            found[-1][2] = True
            continue
        if current is None or starts_item:
            current = [number, [], False]  # the last field: closed by a comment paragraph of its own
            found.append(current)
        current[1].append(line)
    return [(number, "\n".join(lines)) for number, lines, _ in found]


def tests_in(path):
    """{"Class.test_name"} for every test method written in a test file."""
    tree = ast.parse(path.read_text())
    return {f"{node.name}.{item.name}" for node in tree.body if isinstance(node, ast.ClassDef)
            for item in node.body if isinstance(item, ast.FunctionDef) and item.name.startswith("test_")}


def problems(text, tests_dir=TESTS):
    """Why a README is not fully backed: one line per block without a marker and per reference that names no test."""
    found, known = [], {}
    for number, block in blocks(text):
        markers = MARKER.findall(block)
        if not markers:
            found.append(f"line {number}: nothing backs this block: {block.splitlines()[0][:80]}")
        for kind, body in markers:
            if kind not in KINDS:
                continue
            for ref in filter(None, (part.strip() for part in body.split(";"))):
                name, _, test = ref.partition("::")
                path = tests_dir / name
                if not name.startswith(KINDS[kind]) or not path.is_file():
                    found.append(f"line {number}: `{kind}: {ref}` names no {KINDS[kind]}*.py file in tests/")
                elif test not in known.setdefault(name, tests_in(path)):
                    found.append(f"line {number}: `{kind}: {ref}`: {name} has no such test")
            if not body.strip():
                found.append(f"line {number}: an empty `{kind}:` marker")
    return found


class ReadmeClaims(unittest.TestCase):
    def test_every_block_is_backed_by_a_test_or_says_what_else_it_is(self):
        self.assertEqual(problems((HOME / "README.md").read_text()), [])

    def test_a_claim_without_a_test_and_a_test_that_does_not_exist_are_both_caught(self):
        here = "test_readme_claims.py::ReadmeClaims.test_every_path_the_table_names_exists"
        backed = f"# Title\n\nIt does a thing.\n<!-- test: {here} -->\n\n- It does another.\n  <!-- test: {here} -->\n"
        self.assertEqual(problems(backed), [])
        self.assertEqual(problems(backed + "\nIt also makes coffee.\n"), ["line 9: nothing backs this block: It also makes coffee."])
        self.assertEqual(problems(backed + "- A new bullet.\n"), ["line 8: nothing backs this block: - A new bullet."])
        self.assertEqual(problems(f"It flies.\n<!-- test: {here}_not -->\n"),
                         [f"line 1: `test: {here}_not`: test_readme_claims.py has no such test"])
        self.assertEqual(problems("It flies.\n<!-- test: test_missing.py::A.test_b -->\n"),
                         ["line 1: `test: test_missing.py::A.test_b` names no test_*.py file in tests/"])
        self.assertEqual(problems(f"It flies.\n<!-- smoke: {here} -->\n"),  # a default-run test is not a smoke check
                         [f"line 1: `smoke: {here}` names no smoke_*.py file in tests/"])
        self.assertEqual(problems("It flies.\n<!-- test: -->\n"), ["line 1: an empty `test:` marker"])
        self.assertEqual(problems("One.\n<!-- note: a limit -->\n\nTwo.\n\n<!-- steps -->\n\n| a |\n|---|\n\n<!-- note: x -->\n"), [])

    def test_every_path_the_table_names_exists(self):
        readme = (HOME / "README.md").read_text()
        rows = [line for line in readme.splitlines() if line.startswith("| `")]
        self.assertGreater(len(rows), 8)
        for row in rows:
            for path in re.findall(r"`([^`]+)`", row.split("|")[1]):
                self.assertTrue((HOME / path).exists(), path)

    def test_it_names_nobody_but_the_author_and_keeps_the_stated_limits(self):
        readme = " ".join((HOME / "README.md").read_text().split())
        for said in ("**The guards are not a sandbox.** They do not stop a session that deliberately routes around them through another program",
                     "During testing the free OpenCode models hit an account-wide usage limit (HTTP 429)",
                     "**The mechanisms are tested; the model's obedience is not guaranteed.**",
                     "followed a strict five-step order in 2 of 3 runs", "Copyright 2026 Sumit Kumar Pandit"):
            self.assertIn(said, readme)
        self.assertIsNone(re.search(r"\b[A-Z]{2,}-\d+\b|@[a-z0-9-]+\.[a-z]{2,}", readme))  # no ticket key, no address


if __name__ == "__main__":
    unittest.main()
