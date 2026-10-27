---
name: finish
description: Write back what you learned before ZED:DONE. Use as the last step of every assignment.
---
# finish

Before `ZED:DONE` run `zed-employee finish`. It writes the `work` entry from the DB and your plan, stores your
verdict, appends your log line, and refuses what is missing:

- `--verdict clean|blockers` for a `review` or `qa` assignment (not for others); `blockers` also needs
  `--draft <your findings file>`: whoever fixes starts from it
- `--lesson "..."` for each thing that would make you better at this hat next time (zero or more), or
  `--none "why nothing new"`
- `--idea "..."` for each thing you did by hand more than once that a script could do; it becomes a script request
- `--note "..."` one line of what was tricky, added to the work entry

It also refuses while a fact of this repo is still listed as stale: `zed-employee refresh <id>` or
`drop <id> --reason ...` each first.

Store what you learned about a repo with `zed-employee remember`; every entry needs its source, and a fact about
code needs an anchor string that exists in the file. Never write credentials. Memory is a lead, not a source.
