"""The skills the hats ship: each one is listed, shown and named in a worker's first prompt on both runtimes; none
carries an instruction that contradicts how a worker works here; the review skill, the hat and `zed-review` state one
definition of a blocker; and the README says nothing about them that a test here does not prove. No model."""
import contextlib
import io
import re
import subprocess
import unittest
from unittest.mock import patch

import _load

import test_zed_assign as assign
import zedemployee as emp

cli = _load.load("zed-employee")
review = _load.load("zed-review")

SKILLS = {
    "build": ["implement", "code-rules", "completion-evidence", "raise-pr", "address-findings", "finish"],
    "review": ["review-maintainability", "code-rules", "completion-evidence", "finish"],
    "qa": ["qa-web", "bug-report", "completion-evidence", "finish"],
}
RULE_COUNT = 21
README_SENTENCES = (
    "A worker's first prompt names its hat's skills on either runtime, and `zed-employee skill show <name>` prints one",
    "`check-draft` refuses a review blocker that names no ground (`requirement`, `bug` or `rule <n>`)",
)
# what the source text told an agent to do and a worker here must not: ask in a dialog, keep a second plan, hold back
# the commit, report in the chat only, wait for an approval in the chat, hand the review to a subagent
REMOVED = {
    "a built-in question tool": r"ask-the-user|askuserquestion|question tool|question dialog",
    "a second plan location": r"ai-workspace|addendum|plan\.md",
    "do not commit": r"(do not|don't|never) commit|without committing",
    "a chat-only report": r"in the chat|write no (report |findings )?file|report in chat",
    "waiting for approval in chat": r"wait(s|ing)? for (the user's |your |an )?approval|ask the user to approve|user approves",
    "the source's verdict words": r"request_changes|\bapprove\b|awaiting-user",
    "a subagent": r"subagent",
    "one browser tool only": r"do not fall back",
}
NAMES = {
    "a ticket key": r"\b[A-Z]{2,10}-\d+\b",
    "an email address": r"[\w.+-]+@[\w-]+\.\w+",
    "a URL": r"https?://",
    "the source library or its owner": r"(?i)prompt-library|skpstg",
}


def skill_text(name, hat):
    return emp.skill_path(name, hat).read_text()


def shipped_texts():
    """Every skill and charter a hat session can read: {path relative to templates/: text}."""
    files = [p for root in (emp.HATS, emp.SHARED_SKILLS) for p in root.rglob("*.md")]
    return {str(p.relative_to(emp.HATS.parent)): p.read_text() for p in files}


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(argv))
    return code, out.getvalue()


class ListedAndShownTests(unittest.TestCase):
    def test_each_hat_lists_exactly_its_skills(self):
        for hat, skills in SKILLS.items():
            self.assertEqual(emp.load_manifest(hat)["skills"], skills, hat)
            self.assertEqual(emp.validate_manifest(emp.load_manifest(hat), emp.HATS / hat), [], hat)

    def test_skill_list_and_show_print_each_one(self):
        for hat, skills in SKILLS.items():
            listed = run("skill", "list", "--hat", hat)[1].split()
            for skill in skills:
                self.assertIn(skill, listed, f"{hat}/{skill}")
                code, out = run("skill", "show", skill, "--hat", hat)
                self.assertEqual(code, 0, f"{hat}/{skill}")
                self.assertEqual(out.rstrip(), skill_text(skill, hat).rstrip(), f"{hat}/{skill}")
                self.assertTrue(out.startswith(f"---\nname: {skill}\ndescription: "), f"{hat}/{skill}")

    def test_the_command_a_worker_runs_takes_the_hat_from_its_environment(self):
        for hat, skills in SKILLS.items():
            r = subprocess.run([str(_load.HOME / "bin" / "zed-employee"), "skill", "show", skills[0]],
                               capture_output=True, text=True, env={"PATH": "/usr/bin:/bin:/usr/local/bin", "ZED_HAT": hat})
            self.assertEqual((r.returncode, r.stdout.rstrip()), (0, skill_text(skills[0], hat).rstrip()), hat)


class FirstPromptTests(assign.AssignCase):
    """`bin/zed spawn` on each runtime, with herdr and the OpenCode server stubbed: the brief handover names the skills."""

    def assert_names_the_skills(self, hat, prompt):
        self.assertIn(f"You wear the {hat} hat. Skills load when they apply (`zed-employee skill show <name>`): "
                      f"{', '.join(SKILLS[hat])}. ", prompt, hat)

    def test_claude_code(self):
        for hat in SKILLS:
            sid = self.spawn(hat)
            self.assert_names_the_skills(hat, self.mock_prompt_worker.call_args.args[1])
            self.finish(sid)  # one build writes a worktree at a time

    def test_opencode(self):
        for hat in SKILLS:
            server = assign.FakeOpenCodeServer(self)  # each worker has its own server
            with patch.object(self.z, "opencode_api", side_effect=server):
                sid = self.spawn(hat, runtime="opencode")
            self.assert_names_the_skills(hat, server.prompts()[-1])
            self.finish(sid)
        self.mock_prompt_worker.assert_not_called()  # the brief went through the worker's server


class RemovedInstructionTests(unittest.TestCase):
    def test_no_shipped_text_carries_a_removed_instruction(self):
        for path, text in shipped_texts().items():
            for what, pattern in REMOVED.items():
                self.assertIsNone(re.search(pattern, text, re.I), f"{path}: {what}")

    def test_no_shipped_text_names_a_ticket_a_person_or_the_source(self):
        for path, text in shipped_texts().items():
            for what, pattern in NAMES.items():
                self.assertIsNone(re.search(pattern, text), f"{path}: {what}")

    def test_the_patterns_catch_the_source_wording(self):
        source = ("Ask with your ask-the-user tool (`AskUserQuestion` in Claude Code) and wait for the answer",
                  "Change folder: `ai-workspace/changes/<slug>/`", "Do not commit.", "Report in the chat. Write no file",
                  "the user approves each stage", "REQUEST_CHANGES", "Delegate to a subagent", "Do not fall back to anything else.")
        for line in source:
            self.assertTrue([w for w, p in REMOVED.items() if re.search(p, line, re.I)], line)


class GuaranteeTests(unittest.TestCase):
    """The four things a worker does here that the source text contradicted, each stated by the skill that needs it."""

    def assert_says(self, name, hat, *phrases):
        text = skill_text(name, hat)
        for phrase in phrases:
            self.assertIn(phrase, text, f"{hat}/{name}")

    def test_one_plan_the_file_the_brief_names(self):
        self.assert_says("implement", "build", "Write the plan file the brief names", "`zed-plan lint` must pass",
                         "It\n   is the only plan", "tick the step in the plan file")

    def test_questions_go_through_the_relay(self):
        for hat, name in (("build", "implement"), ("build", "code-rules"), ("build", "completion-evidence"),
                          ("review", "review-maintainability"), ("qa", "qa-web")):
            self.assert_says(name, hat, "`ZED:QUESTION`")
        self.assert_says("implement", "build", "What an existing convention of the codebase settles, you decide.")

    def test_a_build_commits_and_a_review_changes_nothing(self):
        self.assert_says("implement", "build", "Commit on your branch", "`ZED:DONE`")
        self.assert_says("review-maintainability", "review", "change no file", "Never post it anywhere.")

    def test_review_and_qa_end_in_the_checked_file_and_finish(self):
        self.assert_says("review-maintainability", "review", "`zed-review check-draft`", "Verdict: clean | blockers",
                         "`zed-employee finish --verdict ...`")
        self.assert_says("qa-web", "qa", "`zed-qa report-check <dir>` must pass before `zed-employee finish`")
        self.assert_says("completion-evidence", "qa", "`clean`", "`blockers`", "`ZED:BLOCKED`")


class ContentTests(unittest.TestCase):
    def test_the_code_rules_are_numbered_and_each_has_a_check_and_an_example(self):
        text = skill_text("code-rules", "build")
        rows = re.findall(r"^\| (\d+) \| (.+?) \| (.+?) \|$", text, re.M)
        self.assertEqual([int(n) for n, _, _ in rows], list(range(1, RULE_COUNT + 1)))
        self.assertTrue(all(rule.strip() and check.strip() for _, rule, check in rows))
        examples = re.findall(r"^(\d+)\. Bad: .+ Good: .+$", text, re.M)
        self.assertEqual([int(n) for n in examples], list(range(1, RULE_COUNT + 1)))
        self.assertEqual(skill_text("code-rules", "review"), text)  # author and reviewer read one copy

    def test_test_first_is_asked_where_the_repository_has_tests(self):
        text = skill_text("implement", "build")
        self.assertIn("Where the repository has tests, work test first:", text)
        self.assertIn("Where\nthe repository has no tests for the code you change, say so in your report", text)

    def test_qa_keeps_its_browser_tools_and_acts_like_a_user(self):
        text = skill_text("qa-web", "qa")
        self.assertIn("use `playwright-cli`, or `agent-browser`", text)
        self.assertIn("`playwright-cli`, or `agent-browser` as fallback", emp.hat_charter("qa"))
        for phrase in ("## Act like a user", "never by CSS or XPath selectors", "Judge it only by what is on screen"):
            self.assertIn(phrase, text)


class BlockerDefinitionTests(unittest.TestCase):
    """One definition, stated by the review skill, the review hat and `zed-review`, and read by `check-draft`."""

    def test_the_skill_names_the_grounds_check_draft_accepts(self):
        text = skill_text("review-maintainability", "review")
        grounds = re.findall(r"^- `(Ground: [^`]+)`: ", text, re.M)
        self.assertEqual(grounds, ["Ground: requirement", "Ground: bug", "Ground: rule <n>"])
        for ground in grounds:
            self.assertTrue(review.GROUND.search(ground.replace("<n>", "7")), ground)
            self.assertIn(ground.split(": ")[1].split()[0], review.__doc__)
        self.assertIn("Only an objective failure is a `[blocker]`", text)
        self.assertIn("are never a\n`[blocker]`", text)

    def test_the_hat_and_zed_review_state_the_same_definition(self):
        self.assertIn("Only an objective failure is a blocker", emp.hat_charter("review"))
        self.assertIn("Only an objective failure is a blocker", review.__doc__)

    def test_the_draft_the_skill_shows_passes_check_draft(self):
        text = skill_text("review-maintainability", "review")
        draft = re.search(r"```\n(- \[blocker\].*?)```", text, re.S).group(1)
        result = review.check_draft(draft.replace("clean | blockers", "blockers"))
        self.assertEqual((result["problems"], result["findings"], result["verdict"]), ([], 2, "blockers"))
        self.assertEqual({f for f in re.findall(r"\[([a-z-]+)\]", text)} - {"blocker", "should-fix", "nit"}, set())

    def test_no_shipped_text_sets_a_score_threshold(self):
        for path, text in shipped_texts().items():
            self.assertIsNone(re.search(r"\d\s*/\s*10\b|\bscore\b", text, re.I), path)


class ReadmeTests(unittest.TestCase):
    def test_the_readme_says_only_what_the_tests_above_prove(self):
        readme = (_load.HOME / "README.md").read_text()
        for sentence in README_SENTENCES:
            self.assertIn(sentence, readme)
        lines = [ln for ln in readme.splitlines() if re.search(r"code[- ]rules|completion[- ]evidence|test[- ]first|"
                                                               r"act like a user|blocker|worker skill|hat's skills", ln, re.I)]
        for line in lines:
            self.assertTrue([s for s in README_SENTENCES if s in line], line)


if __name__ == "__main__":
    unittest.main()
