---
name: address-findings
description: Turn a review or QA findings draft into plan steps and fix them. Use when the brief is a findings draft.
---
# address-findings

1. Read the findings draft from the brief; do not rely on anyone's memory of the work.
2. Make one plan step per finding, with its `file:line` and a "done when" that re-checks it.
3. Fix each, re-run `zed-checks run <repo>`, tick the step.
4. A finding you disagree with: say why in the plan; if it needs a decision that is not yours, `ZED:QUESTION`.
5. Report which findings are fixed and which are not, with the commits.
