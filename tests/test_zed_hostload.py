import argparse
import contextlib
import io
import unittest

import _load

hl = _load.load("zed-hostload")
T = argparse.Namespace(warn_free_pct=20, crit_free_pct=8, warn_swap_gb=4, crit_swap_gb=8, json=False)
VM = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                    23883.
Pages active:                                 253240.
Pages inactive:                               251555.
Pages speculative:                               778.
Pages purgeable:                                 977.
"""


def check(free, swap, procs=(), **kw):
    args = argparse.Namespace(**{**vars(T), **kw})
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = hl.cmd_check(args, lambda: (free, swap, list(procs)))
    return code, out.getvalue()


class HostloadTests(unittest.TestCase):
    def test_parsers(self):
        self.assertEqual(hl.parse_memory_pressure("x\nSystem-wide memory free percentage: 52%\n"), 52)
        self.assertIsNone(hl.parse_memory_pressure("nothing"))
        self.assertEqual(hl.parse_vm_stat(VM, 17179869184), round(100 * (23883 + 251555 + 778 + 977) * 16384 / 17179869184))
        self.assertIsNone(hl.parse_vm_stat("garbage", 1))
        self.assertAlmostEqual(hl.parse_swapusage("vm.swapusage: total = 4096.00M  used = 2048.00M  free = 2048.00M"), 2.0)
        self.assertEqual(hl.parse_swapusage("vm.swapusage: total = 8.00G  used = 3.00G  free = 5.00G  (encrypted)"), 3.0)
        self.assertIsNone(hl.parse_swapusage("?"))
        self.assertEqual(hl.parse_ps("  1 2048 /sbin/launchd\n55 614400 claude\nbad line\n"), [(1, 2048, "launchd"), (55, 614400, "claude")])

    def test_assess_levels_and_fail_open(self):
        self.assertEqual(hl.assess(50, 0.0, T)[0], "OK")
        self.assertEqual(hl.assess(15, 0.0, T)[0], "WARNING")
        self.assertEqual(hl.assess(5, 0.0, T)[0], "CRITICAL")
        self.assertEqual(hl.assess(50, 5.0, T)[0], "WARNING")
        self.assertEqual(hl.assess(15, 9.0, T)[0], "CRITICAL")
        self.assertEqual(hl.assess(None, None, T)[0], "UNKNOWN")
        self.assertEqual(hl.assess(50, None, T)[0], "OK")

    def test_exit_codes_and_report(self):
        procs = [(1, 614400, "claude"), (2, 600000, "claude"), (3, 100, "herdr"), (4, 4194304, "Chrome")]
        code, out = check(50, 0.0, procs)
        self.assertEqual(code, 0)
        self.assertIn("agents 1.16 GB RSS (claude 1.16, herdr 0.0)", out)
        self.assertIn("top: Chrome 4.0 GB", out)
        self.assertEqual(check(15, 0.0)[0], 1)
        self.assertEqual(check(3, 0.0)[0], 2)
        self.assertEqual(check(None, None)[0], 0)
        self.assertIn("do not start another worker", check(3, 0.0)[1])


if __name__ == "__main__":
    unittest.main()
