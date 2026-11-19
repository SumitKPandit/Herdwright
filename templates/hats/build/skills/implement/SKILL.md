---
name: implement
description: Turn the plan into code, test first where the repository has tests, with one self-review and every check. Use at the start of a build assignment's coding steps.
---
# implement

## Before the first change

1. **The plan.** A plan session wrote the file the brief names: read it and follow it. Start at its first unticked
   step; a ticked step is done and its evidence is on its line, so do not redo it. On a small task there is no plan
   yet: Write the plan file the brief names yourself, as the `plan-change` skill says; `hw-plan lint` must pass. It
   is the only plan: Herdwright reads progress from it. Change it only for a concrete reason, recorded in it.
2. Read the code next to what you change; match its patterns, naming and comment density.
3. Run `hw-checks run <repo>` (lint, types, tests) before you change a line. A check that already fails:
   `HW:QUESTION`, never a silent workaround.

## Per plan step

Where the repository has tests, work test first:

1. **Protect existing behaviour.** Add the tests the plan lists under "Existing behaviour to protect", one for each
   behaviour that must stay as it is, and run them. Each
   must pass on today's code. If one fails, today's behaviour is not what the brief says: `HW:QUESTION`.
2. **Add the new tests.** The tests the plan lists under "New tests": one per requirement or scenario of the brief,
   named after it. Run them. Each must fail
   for the reason the change will remove. A failure for another reason, such as an import error or a broken fixture:
   fix the test. A test that already passes: the requirement is met or the test is wrong; find out which.
3. **Change the code.** Before adding a function, search the repo for one that already does it and reuse it. Write the
   least code that makes the step's tests pass, under the `code-rules` skill.
4. Run the step's tests, then tick the step in the plan file and say where its evidence is:
   `- [x] … Evidence: <test name or commit>`. Do not start the next step until they pass. A session that takes over
   starts from the ticks.

Never edit, skip or loosen an existing test to make it pass, unless the brief says its assertion must change. Where
the repository has no tests for the code you change, say so in your report instead of inventing a test framework.

## After the last step

1. **Self-review, in one pass.** Check the full diff against every rule of the `code-rules` skill, using its Check
   column, and fix each rule that fails. Do not repeat the pass: a rule that still fails is listed in your report.
2. **Check everything.** Run `hw-checks run <repo>` again, including the tests from the steps above. All must pass.
   Fix what your change broke. If you cannot after 3 attempts with no new information, stop: `HW:BLOCKED`.
3. Commit on your branch in the user's format.
4. **Report** in `HW:DONE` as the `completion-evidence` skill says: each requirement with its test, each check with
   its command and result, open rule failures, and anything you noticed but did not change.

Anything that is a product, architecture or scope decision, or work the brief does not cover: `HW:QUESTION`, never a
guess. What an existing convention of the codebase settles, you decide.

## Known failure modes

- Making a failing test pass by changing the test instead of the code.
- Cleaning up code next to the change. It is out of scope unless the brief includes it.
