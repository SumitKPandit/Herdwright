---
name: review-maintainability
description: Review a diff for correctness, the code rules and long-term maintainability. Use for the judgement part of a review assignment.
---
# review-maintainability

Start from the facts `zed-review prep` printed (changed files without tests, debug leftovers, new dependencies,
migrations, large functions); do not redo them by hand. You review a change someone else wrote: change no file.

## Review

1. Read the change: `git diff <base branch>...HEAD`, and the brief it implements. Review only changed lines; read the
   code around them for context.
2. **Correctness.** The change does what the brief says. For every requirement and scenario of the brief, name the
   test and the assertion that check it, and run that test (running tests changes no file). Where the repository has
   tests, a requirement without one is a finding. Where it has none for the changed code, that is no finding: list the
   requirement under not checked, as `not-applicable`, and say the repository has no tests. Then, for each function
   the diff adds or changes:
   - List its inputs: parameters, values returned by the functions it calls, and state it reads. Run each through
     every case: null or missing, empty, one item, boundary values, duplicates, malformed input, a call it makes
     failing or timing out, and two calls at once. Trace each case through the changed code. A case with a wrong
     result is a finding.
   - If the change alters its signature, return value, errors, side effects or their order, search for every caller,
     including callers the diff does not touch, and confirm each still works. A caller that breaks is a finding on
     the changed line.
3. **Rules.** Check every changed hunk against every rule of the `code-rules` skill, using its Check column. Add no
   rules of your own.
4. Skip anything a linter or formatter catches, such as whitespace, formatting and import order.
5. **Verify each finding** before you write it. Re-read the code and its callers and confirm the problem happens. A
   correctness finding names the input, the code path and the wrong result. Drop a finding you cannot support with
   evidence. A doubt about intent that neither the code nor the brief settles is a `ZED:QUESTION`, not a blocker.
6. Follow the `completion-evidence` skill: list what you did not check, as `required` or `not-applicable`, with the
   reason. Required work left unchecked cannot give a clean verdict.

## What blocks

Only an objective failure is a `[blocker]`, and each one names its ground:

- `Ground: requirement`: where the repository has tests, a requirement of the brief has no passing test; or a required
  check of the repository fails.
- `Ground: bug`: a logic bug you can show with a concrete failing input.
- `Ground: rule <n>`: a rule of the `code-rules` skill fails its Check.

Everything else is `[should-fix]` (a clear improvement no rule requires; the author decides) or `[nit]` (a small
clarity point) and never blocks. Naming taste, structure, and DRY or SOLID beyond the code rules are never a
`[blocker]`. Never suggest an alternative that only works as well as the current code, and never call code
"confusing" or say it "needs refactoring" without the mechanical reason.

| Do | Don't |
|---|---|
| Name the rule number, or the input, path and wrong result | Say "I'd write this differently" |
| Give the specific fix for a blocker | Say "this could be cleaner" |
| Stay on changed lines | Flag code the change did not touch |
| Put every blocker in the first review | Hold a blocker back for the next review |
| List should-fix and nit findings in the draft | Block on them |

A re-review after fixes re-checks the earlier blockers and the code the fixes changed, and raises nothing new on code
the fixes did not touch.

## The draft

Write the findings draft, most severe first, one line per finding, then exactly one verdict:

```
- [blocker] path/file.py:42 what is wrong: the input, the path and the wrong result. Ground: bug. Fix: the change that would fix it
- [should-fix] path/file.py:10 what would improve and why. Fix: the change
Verdict: clean | blockers
```

`blockers` when any finding is a `[blocker]`, otherwise `clean`. Check the draft with `zed-review check-draft`, then
`zed-employee finish --verdict ...`. Never post it anywhere.
