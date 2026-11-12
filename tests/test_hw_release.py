"""What the published tree must carry: the licence files, the attribution of the files ported from firstmate, and a
profile template whose workers cannot post to the tracker or the wiki until the user edits the list."""
import hashlib
import json
import shutil
import unittest

from test_hw_assign import HOME, AssignCase

APACHE_2_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"  # apache.org/licenses/LICENSE-2.0.txt
FIRSTMATE_NOTICE = "Copyright (c) 2026 Kun Chen"
PORTED = ("bin/lib/hwlib.py", "bin/hw-crew", "bin/hw-guard", "bin/hw-plan", "bin/hw-pr", "bin/hw-pretool")
TEMPLATE_SETTINGS = HOME / "profile.template" / "config" / "settings.json"
WRITE_TOOLS = ("addGraphContext", "addOrEditJiraIssueComment", "createConfluenceContent", "createJiraIssue", "editJiraIssue",
               "executeDestructive", "executeWrite", "transitionJiraIssue", "updateConfluenceContent")


class LicenceTests(unittest.TestCase):
    def test_licence_is_the_unmodified_apache_2_text(self):
        self.assertEqual(hashlib.sha256((HOME / "LICENSE").read_bytes()).hexdigest(), APACHE_2_SHA256)

    def test_notice_names_the_project_and_firstmate(self):
        notice = (HOME / "NOTICE").read_text()
        self.assertIn("Copyright 2026 Sumit Kumar Pandit", notice)
        self.assertIn(FIRSTMATE_NOTICE, notice)
        self.assertIn("THIRD_PARTY_LICENSES.md", notice)

    def test_third_party_file_keeps_the_firstmate_notice_the_mit_text_and_the_files_it_covers(self):
        text = (HOME / "THIRD_PARTY_LICENSES.md").read_text()
        for part in ("MIT License", FIRSTMATE_NOTICE, "Permission is hereby granted, free of charge, to any person obtaining a copy",
                     "The above copyright notice and this permission notice shall be included in all",
                     'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR',
                     "https://github.com/kunchenguid/firstmate", *PORTED):
            self.assertIn(part, text)

    def test_each_ported_file_attributes_firstmate(self):
        for name in PORTED:
            head = (HOME / name).read_text()[:1200]
            self.assertIn(f"firstmate: {FIRSTMATE_NOTICE}, MIT licence, see THIRD_PARTY_LICENSES.md.", head, name)

    def test_no_other_file_says_it_was_ported_from_firstmate(self):
        sources = [p for p in (HOME / "bin").rglob("*") if p.is_file() and p.suffix != ".pyc"]
        ported = {str(p.relative_to(HOME)) for p in sources if "firstmate" in p.read_text()}
        self.assertEqual(ported, set(PORTED))


class TemplateDenyTests(AssignCase):
    def test_a_session_built_from_the_template_denies_the_tracker_and_wiki_write_tools(self):
        shutil.copy(TEMPLATE_SETTINGS, self.profile / "config" / "settings.json")
        expected = [f"mcp__claude_ai_Atlassian_MCP__{name}" for name in WRITE_TOOLS]
        for hat in ("build", "review", "qa"):
            kit, _ = self.z.hat_kit(hat, 7, f"{hat}-t20", self.root / "wt")
            self.assertEqual(json.loads(kit["claude_settings"].read_text())["permissions"]["deny"], expected, hat)


if __name__ == "__main__":
    unittest.main()
