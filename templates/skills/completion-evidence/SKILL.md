---
name: completion-evidence
description: What counts as proof that work is complete. Use before reporting a build as done or giving a review or QA verdict.
---
# completion-evidence

Missing information is not approval. A summary, or an empty list of failures, is not proof of completion. A result
describes the code as it was when it ran: after any code change, run it again.

## What to record

- **Plan:** your session's plan file, every step ticked. A step that is not done stays unticked; say why.
- **Checks:** the repo's required checks, from `zed-checks run <repo>` or, where the repo has no `checks` block, from
  its manifests, CI config and instructions, with where you found each. For each: the command, the exit code and what
  it printed. A failure is accepted only if the brief, or an answer Zed relayed, accepted that exact failure.
- **Requirements:** for each requirement and scenario the brief states, the test and assertion that check it, and the
  result.
- **QA cases:** the full list, each with its result and evidence. An empty list, a blocked case or a case that did not
  run cannot pass.
- **Not checked:** each piece of work you skipped, marked `required` or `not-applicable`, with the reason. Never infer
  that skipped work is irrelevant.

## Outcomes

- A build reports done (`ZED:DONE`) only when the scope of the brief is implemented, every required check ran with no
  failure that was not accepted, and the plan is ticked. That is "implemented, awaiting review and QA", not finished
  work: say so, and list what you noticed but did not change.
- A review or QA gives `clean` only when its work is complete and current: every requirement or case checked, none
  failed, nothing `required` left unchecked. Anything else is `blockers`, with the findings file.
- A decision that is missing: `ZED:QUESTION`. An ambiguous, unrelated or missing answer is not a resolution; ask again.
- A capability, setup, check or piece of evidence that is unavailable: `ZED:BLOCKED`, naming what is missing. Do not
  run later steps as though it had passed.
- A run that failed is never `clean` and never done.

A clean, complete change needs no manufactured finding and no retry.
