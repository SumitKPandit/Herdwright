"""`bin/zed spawn employee= hat=` (design step 3.1/3.2): the hard rule, the one-writer rule, queuing, the files an
employee's session starts with and the policy rendered per runtime. herdr and the host load check are stubbed; git
and the guard installation are real, in a throwaway repo and profile."""
import contextlib
import io
import json
import os
import sqlite3
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import _load  # noqa: F401  (puts bin/lib on sys.path)

import zedemployee as emp
import zedlib

HOME = Path(__file__).resolve().parents[1]
BRIEF = "# Brief\n\n## Plan\n- one\n\n## Definition of done\n- done\n"


class AssignCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.profile = self.root / "profile"
        self.repo = self.root / "api"
        self.wt_root = self.root / "wt"
        (self.profile / "config").mkdir(parents=True)
        (self.profile / "profile.md").write_text("# test profile\n")
        self.repo.mkdir()
        for cmd in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
            zedlib.git(self.repo, *cmd)
        (self.repo / "guard.py").write_text("def require_role(user): ...\n")
        zedlib.git(self.repo, "add", ".")
        zedlib.git(self.repo, "commit", "-qm", "init")
        (self.profile / "config" / "projects.json").write_text(json.dumps(
            {"worktree_root": str(self.wt_root), "repos": {"api": {"path": str(self.repo), "dev_base": "main"}}}))
        (self.profile / "config" / "runtimes.json").write_text(json.dumps({"order": ["claude", "copilot"]}))
        for name, value in (("PROFILE_DIR", self.profile), ("DB_PATH", self.profile / "db" / "zed.db"),
                            ("PROJECTS_PATH", self.profile / "config" / "projects.json")):
            patcher = patch.object(zedlib, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.z = types.ModuleType("zed")
        self.z.__file__ = str(HOME / "bin/zed")
        with patch.dict(os.environ, {"ZED_PROFILE_DIR": str(self.profile)}):
            exec(compile((HOME / "bin/zed").read_text(), self.z.__file__, "exec"), self.z.__dict__)
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(contextlib.redirect_stderr(self.output))
        self.conn = self.z.db()
        for name in ("juno", "quill", "pip"):
            emp.hire(name)
        ts = "2026-10-27 09:00"
        self.conn.execute("INSERT INTO tasks(id,title,jira,worktype,status,created_at,updated_at) VALUES (20,'Role guard','MENT-20','story','active',?,?)", (ts, ts))
        self.conn.commit()
        self.brief = self.root / "brief.md"
        self.brief.write_text(BRIEF)
        self.branch = "story/ment-20-guard"
        self.herdr_calls = []
        real_run = self.z.run

        def fake_run(cmd, check=True, cwd=None):
            if cmd[0] == "herdr" or any("zed-hostload" in str(c) for c in cmd):
                self.herdr_calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return real_run(cmd, check=check, cwd=cwd)

        def fake_herdr_json(args):
            self.herdr_calls.append(["herdr", *args])
            return {"result": {"workspace": {"workspace_id": "w1"}, "root_pane": {"pane_id": "p1"}}}

        for target, kwargs in (("run", {"side_effect": fake_run}), ("herdr_json", {"side_effect": fake_herdr_json}),
                               ("require_lock", {}), ("choose_runtime", {"return_value": "claude"}),
                               ("quota_warning", {"return_value": None}), ("require_capacity", {}),
                               ("prompt_worker", {"return_value": (True, "")})):
            patcher = patch.object(self.z, target, **kwargs)
            setattr(self, f"mock_{target}", patcher.start())
            self.addCleanup(patcher.stop)
        patcher = patch.object(self.z.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        wt = self.wt_root / "api" / "ment-20-guard"
        wt.parent.mkdir(parents=True)
        zedlib.git(self.repo, "worktree", "add", "-q", "-b", self.branch, str(wt))
        self.worktree = wt

    def spawn(self, employee, hat, **extra):
        args = ["20", "repo=api", "base=main", f"branch={self.branch}", f"brief={self.brief}",
                f"employee={employee}", f"hat={hat}", *[f"{k}={v}" for k, v in extra.items()]]
        return self.z.cmd_spawn(args)

    def refused(self, employee, hat, text, code=1, **extra):
        self.output.truncate(0)
        with self.assertRaises(SystemExit) as e:
            self.spawn(employee, hat, **extra)
        self.assertEqual(e.exception.code, code)
        self.assertIn(text, self.output.getvalue())

    def finish(self, sid, verdict=None):
        self.conn.execute("UPDATE sessions SET status='finished', verdict=? WHERE id=?", (verdict, sid))
        self.conn.commit()

    def session(self, sid):
        return self.conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()


class SpawnWithEmployeeTests(AssignCase):
    def test_build_records_the_assignment_and_writes_the_session_files(self):
        sid = self.spawn("juno", "build", tags="auth,views")
        s = self.session(sid)
        self.assertEqual((s["employee"], s["hat"], s["origin"], s["agent"]), ("juno", "build", "zed", "juno-t20-build"))
        out = self.profile / ".cache" / "employees" / "juno"
        self.assertTrue((out / f"S{sid}-pack.md").exists())
        rules = (out / "juno-t20-build-rules.md").read_text()
        self.assertIn("You are **Juno**", rules)
        self.assertIn("Hat: build", rules)
        self.assertIn(str(out / f"S{sid}-pack.md"), rules)
        self.assertEqual(rules, (out / "juno-t20-build-instructions" / "zed-worker.instructions.md").read_text())
        workspace = next(c for c in self.herdr_calls if c[:3] == ["herdr", "workspace", "create"])
        env = [workspace[i + 1] for i, a in enumerate(workspace) if a == "--env"]
        self.assertEqual(env, ["ZED_EMPLOYEE=juno", "ZED_HAT=build", f"ZED_SESSION=S{sid}", f"ZED_WORKTREE={self.worktree}"])
        start = next(c for c in self.herdr_calls if c[:3] == ["herdr", "agent", "start"])
        self.assertIn(str(out / "juno-t20-build-rules.md"), start)
        self.assertEqual(start[start.index("--settings") + 1], str(out / "juno-t20-build-settings.json"))
        hook = json.loads((out / "juno-t20-build-settings.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual(hook["command"], str(HOME / "bin" / "zed-deny"))
        prompt = self.mock_prompt_worker.call_args.args[1]
        self.assertIn(f"You are Juno, a named employee wearing the build hat. Read your memory pack at {out / f'S{sid}-pack.md'}", prompt)
        self.assertIn("zed-employee finish", prompt)
        self.assertIn(str(out / "juno-t20-build-rules.md"), prompt)

    def test_review_is_refused_for_an_author_and_accepted_for_someone_else(self):
        self.finish(self.spawn("juno", "build"))
        self.refused("juno", "review", "nobody reviews or QAs their own work")
        self.refused("juno", "qa", "nobody reviews or QAs their own work")
        sid = self.spawn("quill", "review")
        self.assertEqual(self.session(sid)["hat"], "review")

    def test_a_designer_is_an_author_too_and_every_fixer_joins_the_set(self):
        self.conn.execute("INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,status,employee,hat,created_at,updated_at) "
                          "VALUES (20,'api','main',?,?,'pip-t20-design','finished','pip','design','x','x')", (self.branch, str(self.worktree)))
        self.conn.commit()  # the design hat ships later (step 7); its sessions count as authorship already
        self.refused("pip", "review", "nobody reviews")
        self.finish(self.spawn("juno", "build"))
        self.finish(self.spawn("quill", "build"))  # quill fixes
        self.refused("quill", "review", "nobody reviews")

    def test_the_authors_of_a_branch_count_even_on_another_task(self):
        self.finish(self.spawn("juno", "build"))
        ts = "2026-10-27 09:00"
        self.conn.execute("INSERT INTO tasks(id,title,status,created_at,updated_at) VALUES (21,'Follow-up','active',?,?)", (ts, ts))
        self.conn.commit()
        self.output.truncate(0)
        with self.assertRaises(SystemExit):
            self.z.cmd_spawn(["21", "repo=api", "base=main", f"branch={self.branch}", f"brief={self.brief}", "employee=juno", "hat=qa"])
        self.assertIn("nobody reviews", self.output.getvalue())

    def test_a_build_is_refused_while_a_reader_is_open_and_readers_may_share(self):
        self.finish(self.spawn("juno", "build"))
        reader = self.spawn("quill", "review")
        self.refused("pip", "build", "one writer per worktree")
        qa = self.spawn("pip", "qa")  # a second reader on the same worktree
        self.assertEqual(self.session(qa)["hat"], "qa")
        self.finish(reader)
        self.finish(qa)
        self.assertTrue(self.spawn("pip", "build"))  # nothing open any more

    def test_a_reader_is_refused_while_a_build_is_open(self):
        self.spawn("juno", "build")
        self.refused("quill", "review", "juno-t20-build) is writing")

    def test_a_plain_worker_on_the_worktree_counts_as_a_writer(self):
        self.conn.execute("INSERT INTO sessions(task_id,repo,base,branch,worktree,agent,status,created_at,updated_at) "
                          "VALUES (20,'api','main',?,?,'t20-api','running','x','x')", (self.branch, str(self.worktree)))
        self.conn.commit()
        self.refused("quill", "review", "one writer per worktree")

    def test_a_busy_employee_is_queued_not_refused(self):
        self.finish(self.spawn("juno", "build"))
        self.spawn("quill", "review")
        self.refused("quill", "qa", "quill is busy with S2", code=75)

    def test_employee_and_hat_go_together_and_must_exist(self):
        self.output.truncate(0)
        with self.assertRaises(SystemExit):
            self.z.cmd_spawn(["20", "repo=api", "base=main", f"branch={self.branch}", f"brief={self.brief}", "employee=juno"])
        self.assertIn("go together", self.output.getvalue())
        self.refused("nobody", "build", "no employee 'nobody'")
        self.refused("juno", "juggle", "unknown hat")
        self.refused("juno", "build", "origin must be", origin="robot")

    def test_assign_is_spawn_with_the_employee_first(self):
        sid = self.z.cmd_assign(["juno", "20", "hat=build", "repo=api", "base=main", f"branch={self.branch}", f"brief={self.brief}",
                                 "origin=user"])
        self.assertEqual((self.session(sid)["employee"], self.session(sid)["origin"]), ("juno", "user"))

    def test_a_same_named_open_session_gets_a_suffix(self):
        self.finish(self.spawn("juno", "build"))  # finished but not closed: its agent name is taken
        self.finish(self.spawn("quill", "review"))
        sid = self.spawn("quill", "review")
        self.assertEqual(self.session(sid)["agent"], f"quill-t20-review-{sid}")

    def test_the_employees_runtime_preference_and_hat_kind_are_used(self):
        edit = self.profile / "employees" / "quill" / "employee.md"
        edit.write_text(edit.read_text().replace("runtime: auto", "runtime: copilot"))
        with patch.object(self.z, "choose_runtime", return_value="copilot") as choose, \
                patch.object(self.z, "set_copilot_permissions", return_value=None):
            self.finish(self.spawn("juno", "build"))
            self.spawn("quill", "review")
        self.assertEqual([c.args[0] for c in choose.call_args_list], ["auto", "copilot"])

    def test_findings_of_earlier_assignments_are_named_in_the_first_prompt(self):
        self.z.log_event(self.conn, 20, "findings", "S2 quill review blockers: /tmp/findings.md")
        self.conn.commit()
        self.finish(self.spawn("quill", "review"))
        self.spawn("juno", "build")
        self.assertIn("/tmp/findings.md", self.mock_prompt_worker.call_args.args[1])

    def test_a_handoff_keeps_the_assignment(self):
        sid = self.spawn("juno", "build")
        s = self.session(sid)
        (self.profile / "briefs").mkdir()
        with patch.object(self.z, "close_workspace"), patch.object(self.z, "record_credits"), \
                patch.object(self.z, "record_spend"), patch.object(self.z, "cmd_spawn", return_value=9) as spawn:
            self.z.cmd_handoff([f"S{sid}", "restart"])
        args = spawn.call_args.args[0]
        self.assertIn("employee=juno", args)
        self.assertIn("hat=build", args)
        self.assertIn(f"name={s['agent']}", args)


class RenderedPolicyTests(AssignCase):
    def kit(self, hat):
        return self.z.employee_kit("quill", hat, "api", [], 7, "quill-t20", self.worktree)

    def test_claude_gets_a_settings_hook_and_copilot_a_plugin_and_deny_flags(self):
        kit, env = self.kit("review")
        _, claude = self.z.runtime_launch("claude", "quill-t20", None, None, kit)
        self.assertEqual(claude[claude.index("--settings") + 1], str(kit["claude_settings"]))
        self.assertEqual(claude[claude.index("--append-system-prompt-file") + 1], str(kit["rules"]))
        cenv, copilot = self.z.runtime_launch("copilot", "quill-t20", None, None, kit)
        self.assertEqual(cenv, [f"COPILOT_CUSTOM_INSTRUCTIONS_DIRS={kit['instructions']}"])
        self.assertEqual(copilot[copilot.index("--plugin-dir") + 1], str(kit["copilot_plugin"]))
        for prefix in ("gh pr review", "gh pr comment", "bb pr comment"):
            self.assertIn(f"--deny-tool=shell({prefix})", copilot)
        hooks = json.loads((kit["copilot_plugin"] / "hooks" / "hooks.json").read_text())
        self.assertEqual(hooks["hooks"]["PreToolUse"][0]["bash"], str(HOME / "bin" / "zed-deny"))
        self.assertEqual(json.loads((kit["copilot_plugin"] / "plugin.json").read_text())["name"], "zed-deny")
        self.assertIn("ZED_HAT=review", env)

    def test_a_plain_worker_is_launched_as_before(self):
        _, claude = self.z.runtime_launch("claude", "t1-api", None, None)
        self.assertNotIn("--settings", claude)
        env, copilot = self.z.runtime_launch("copilot", "t1-api", None, None)
        self.assertEqual(env, [f"COPILOT_CUSTOM_INSTRUCTIONS_DIRS={self.z.COPILOT_INSTRUCTIONS_DIR}"])
        self.assertNotIn("--plugin-dir", copilot)


if __name__ == "__main__":
    unittest.main()
