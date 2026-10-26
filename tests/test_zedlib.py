import sys
import unittest

import _load  # noqa: F401  (puts bin/lib on sys.path)
import zedlib


class ZedlibTests(unittest.TestCase):
    def test_run_bounded_ok_missing_and_timeout(self):
        self.assertTrue(zedlib.run_bounded(["echo", "hi"]).ok)
        self.assertEqual(zedlib.run_bounded(["no-such-binary-zed"]).returncode, 127)
        r = zedlib.run_bounded([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)
        self.assertTrue(r.timed_out)
        self.assertFalse(r.ok)

    def test_cap_lines(self):
        out = zedlib.cap_lines([f"line {i}" for i in range(5)], limit=3)
        self.assertEqual(out, ["line 0", "line 1", "line 2", "... 2 more omitted"])
        self.assertEqual(zedlib.cap_lines(["x" * 50], width=10), ["x" * 9 + "…"])

    def test_parse_plan_steps(self):
        steps = zedlib.parse_plan_steps("- [x] 1. a\n- [ ] 2. b\nnot a step\n  - [X] 3. c\n")
        self.assertEqual([(s["n"], s["done"]) for s in steps], [(1, True), (2, False), (3, True)])


if __name__ == "__main__":
    unittest.main()
