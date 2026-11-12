---
name: implement
description: Turn the plan into code, test first where the repository has tests, with one self-review and every check. Use at the start of a build assignment's coding steps.
---
# implement

## Before the first change

1. Write the plan file the brief names (steps, each with "done when" and an estimate); `zed-plan lint` must pass. It
   is the only plan: Zed reads progress from it.
2. Read the code next to what you change; match its patterns, naming and comment density.
3. Run `zed-checks run <repo>` (lint, types, tests) before you change a line. A check that already fails:
   `ZED:QUESTION`, never a silent workaround.

## Per plan step

Where the repository has tests, work test first:

1. **Protect existing behaviour.** Add a test for each behaviour the brief says must stay as it is, and run it. It
   must pass on today's code. If it fails, today's behaviour is not what the brief says: `ZED:QUESTION`.
2. **Add the new tests.** One test per requirement or scenario of the brief, named after it. Run them. Each must fail
   for the reason the change will remove. A failure for another reason, such as an import error or a broken fixture:
   fix the test. A test that already passes: the requirement is met or the test is wrong; find out which.
3. **Change the code.** Before adding a function, search the repo for one that already does it and reuse it. Write the
   least code that makes the step's tests pass, under the `code-rules` skill.
4. Run the step's tests, then tick the step in the plan file. Do not start the next step until they pass.

Never edit, skip or loosen an existing test to make it pass, unless the brief says its assertion must change. Where
the repository has no tests for the code you change, say so in your report instead of inventing a test framework.

## After the last step

1. **Self-review, in one pass.** Check the full diff against every rule of the `code-rules` skill, using its Check
   column, and fix each rule that fails. Do not repeat the pass: a rule that still fails is listed in your report.
2. **Check everything.** Run `zed-checks run <repo>` again, including the tests from the steps above. All must pass.
   Fix what your change broke. If you cannot after 3 attempts with no new information, stop: `ZED:BLOCKED`.
3. Commit on your branch in the user's format.
4. **Report** in `ZED:DONE` as the `completion-evidence` skill says: each requirement with its test, each check with
   its command and result, open rule failures, and anything you noticed but did not change.

Anything that is a product, architecture or scope decision, or work the brief does not cover: `ZED:QUESTION`, never a
guess. What an existing convention of the codebase settles, you decide.

## Known failure modes

- Making a failing test pass by changing the test instead of the code.
- Cleaning up code next to the change. It is out of scope unless the brief includes it.
