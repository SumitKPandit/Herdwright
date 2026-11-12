---
name: finish
description: End the session before HW:DONE. Use as the last step of every assignment.
---
# finish

Before `HW:DONE` run `hw-employee finish`. It ticks the plan's last step, stores your verdict with the code it is
about, and refuses what is missing:

- `--verdict clean|blockers` for a `review` or `qa` session (not for others); `blockers` also needs
  `--draft <your findings file>`: whoever fixes starts from it
- a review or qa that changed the code is its author: no verdict, ask Herdwright for a fresh session

What you learned about the repo goes in the `Learnings:` line of your `HW:DONE` block; Herdwright files it.
