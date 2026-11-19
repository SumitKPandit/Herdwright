---
name: plan-change
description: Write the plan for a bug fix, feature, refactor, config or docs change before any code changes, test first. Use for a plan assignment, and in a build of a small task that writes its own plan.
---
# plan-change

The plan is one file, the one the brief names. A build follows it step by step and ticks it, so a later session knows
what is done. Start it from the template at the end of this skill and keep every section; write "None" where a section
does not apply, so the reader knows it was checked.

## Rules

- Never assume. Write only facts you verified in this session, each with its file path and function, class or config
  key, never a line number. What the brief does not settle (a requirement, the scope, what "done" means, which approach,
  anything others rely on such as an API, a schema or a config key, a new dependency, a deletion) is an `HW:QUESTION`,
  even when you see only one option. Name the option you recommend and why.
- Tests come first. Describe each test by the behaviour it checks, not by the planned implementation, so it holds
  whichever way the code is written.
- Refactoring is planned only when the brief asks for it, and last, with every test passing.
- A plan session changes no code, tests or config: it writes the plan file only.

## Steps

1. **Understand the request.** From the brief: for a bug, what happens, what should happen and how to reproduce it;
   for a feature, who needs what and why; what "done" means; what is out of scope. Pick the mode: `feature`, `bugfix`,
   `refactor` (behaviour stays, so "Existing behaviour to protect" is the core and "New tests" is "None"), `config`
   (done when names the observable effect and the command that shows it) or `docs` (done when lists the docs and the
   check that paths and links resolve).
2. **Explore.** Read the code involved. Find:
   - for a bug, the root cause, reproduced by running the code or an existing test where you can. A symptom is not a
     cause.
   - code to reuse, where new code belongs, and every other place the same rule lives, with its callers
   - every existing behaviour the change touches, and the test that covers each one, or "no test"
   - what else the change could break: data, APIs, other modules, deployment
3. **Existing behaviour to protect.** List every behaviour the change touches and must keep. "Covered by" names the
   existing test, or says "no test"; every "no test" row gets a test to add that passes on today's code.
4. **New tests.** One row per requirement or scenario of the brief: the test, named after it, and why it fails today.
   Cover the normal case, the edge cases and the errors the brief names. For a bug, one test reproduces it. List the
   existing tests that assert behaviour the brief changes, with their new expectation.
5. **Steps.** The checklist the build ticks, one line per step: `- [ ] 1. <step> — done when <check>. Tests: <names>.
   ~<time>`. Order them: add the protecting tests and see them pass; add the new tests and see each fail for the reason
   stated; then change the code, each step a slice that can be checked on its own. A step about merging, pushing or
   raising a PR carries `[after-review]`. The last line is the fixed one of the brief's Plan section.
6. **Where the repository has no tests** for the code that changes, say so in both test sections ("None: the
   repository has no tests for this code") and write `Tests: none` on the steps, with the command or observation that
   shows each step done instead. Do not plan a test framework the brief did not ask for.
7. **Check.** `hw-plan check <plan file>` must pass: it reads the two test sections and each step's "done when",
   tests and estimate.

A build ticks a step as `- [x] … Evidence: <test name or commit>`. It changes the plan only for a concrete reason,
recorded under "Changes to this plan".

In a build of a small task you write this plan yourself, to the same rules, before the first change.

## Known failure modes

- Treating a choice that shapes the change as cheap or obvious instead of asking.
- Writing tests against the planned implementation, so they must be rewritten when the approach changes.
- A step that cannot be checked on its own, so a restarted build cannot tell whether it is done.

## Plan template

Keep every section. Write "None" when a section does not apply. Keep each section as short as the change allows.
Point to code by path and symbol, never by line number.

```markdown
# Plan: <task id> <title>
Mode: feature | bugfix | refactor | config | docs

## Problem
<Bug: what happens, what should happen, how to reproduce it, and the root cause with its evidence. Feature: who needs
what, and why.>

## Done when
<Every test below passes, the existing suite passes, and any other check the brief names.>

## Out of scope
<What this change will not do.>

## Existing behaviour to protect
| Behaviour that must stay | Covered by | Test to add (passes today) |
|---|---|---|
<Every behaviour the change touches and must keep. "Covered by" names an existing test or says "no test"; every "no
test" row gets a test to add.>

## New tests
| Requirement of the brief | Test | Fails today because |
|---|---|---|
<One row per requirement or scenario. For a bug, one row reproduces it.>

## Existing tests to update
| Test | Asserts today | New expectation |
|---|---|---|
<Tests that assert behaviour the brief changes.>

## Context
<Reusable code, where new code goes and the types to extend, each as path: symbol and its role.>

## Risks and rollback
| Risk | Likelihood | Impact | Mitigation | Rollback |
|---|---|---|---|---|

## Stop and re-plan if
<Findings during the build that would make this plan wrong.>

## Steps
- [ ] 1. Add the tests in "Existing behaviour to protect" — done when they pass on today's code. Tests: <names>. ~20m
- [ ] 2. Add the tests in "New tests" — done when each fails for the reason stated. Tests: <names>. ~30m
- [ ] 3. <What changes> (<path: symbol>) — done when <its tests> pass. Tests: <names>. ~40m
- [ ] 4. Raise a draft PR against <base> [after-review] — done when it is open. ~10m
- [ ] Run hw-employee finish and report (HW:DONE)

## Changes to this plan
<Each change after the build started, with its reason. "None" until then.>
```
