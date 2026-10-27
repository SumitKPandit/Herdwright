---
name: implement
description: Turn the plan into code with checks and a self-review. Use at the start of a build assignment's coding steps.
---
# implement

1. Write the plan file first (steps, each with "done when" and an estimate); `zed-plan lint` must pass.
2. Read the code next to what you change; match its patterns, naming and comment density.
3. Per step: change, run `zed-checks run <repo>` (lint, types, tests), tick the step in the plan.
4. A contract that other repos consume changes? Run `zed-xref <symbol>` and say so in the plan.
5. Self-review the diff before the PR: no debug leftovers, no dead code, tests where the repo has them.
6. Anything that is a product, architecture or scope decision: `ZED:QUESTION`, never a guess.
