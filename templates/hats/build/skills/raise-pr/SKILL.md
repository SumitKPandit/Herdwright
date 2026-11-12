---
name: raise-pr
description: Open the draft PR and take it to ready. Use once the plan's steps are done and checks pass.
---
# raise-pr

1. Commit on your branch in the user's format; never push a base branch.
2. Open the PR as a draft with the title format, base branch and description the user's rules give.
3. `hw-pr wait <pr-url>` until CI settles; fix red CI.
4. `hw-pr ready-check <pr-url>` must pass before marking it ready; then request the listed reviewers.
5. Report the PR in `HW:DONE`.
