import json
import os
import re
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

import _load

ex = _load.load("zed-export")
SCHEMA = re.search(r'SCHEMA = """(.*?)"""', (_load.HOME / "bin" / "zed").read_text(), re.S).group(1)
BIN = str(_load.HOME / "bin" / "zed-export")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prof = Path(self.tmp.name) / "profile"
        (self.prof / "db").mkdir(parents=True)
        conn = sqlite3.connect(self.prof / "db" / "zed.db")
        conn.executescript(SCHEMA + "CREATE TABLE extra (id INTEGER PRIMARY KEY, v TEXT);")
        conn.execute("INSERT INTO tasks (id,title,status,created_at,updated_at) VALUES (1,'t','active','x','x')")
        conn.execute("INSERT INTO extra (v) VALUES ('later table')")
        conn.commit()
        conn.close()
        self.env = {**os.environ, "ZED_PROFILE_DIR": str(self.prof)}

    def test_export_roundtrip_includes_later_tables(self):
        out = Path(self.tmp.name) / "e.json"
        r = subprocess.run([BIN, "export", str(out)], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("tasks 1", r.stderr)
        data = json.loads(out.read_text())
        self.assertEqual(data["tables"]["tasks"][0]["title"], "t")
        self.assertEqual(data["tables"]["extra"], [{"id": 1, "v": "later table"}])
        v = subprocess.run([BIN, "verify", str(out)], capture_output=True, text=True)
        self.assertEqual((v.returncode, v.stdout.startswith("ok: ")), (0, True))

    def test_stdout_and_missing_db(self):
        r = subprocess.run([BIN, "export"], env=self.env, capture_output=True, text=True)
        self.assertEqual(json.loads(r.stdout)["version"], 1)
        r = subprocess.run([BIN, "export"], env={**os.environ, "ZED_PROFILE_DIR": self.tmp.name + "/none"}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_verify_problems(self):
        self.assertEqual(ex.verify({"format": "x"}), ["not a zed-export file"])
        bad = {"format": "zed-export", "version": 2, "counts": {"tasks": 5}, "tables": {"tasks": [{}]}}
        text = "\n".join(ex.verify(bad))
        for needle in ("unsupported version 2", "count 5 != 1", "missing table sessions"):
            self.assertIn(needle, text)
        r = subprocess.run([BIN, "verify", "/nonexistent.json"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
