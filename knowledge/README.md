# Zed knowledge base

Everything Zed has learned that should outlive a conversation. Zed reads this index at the
start of every session and opens the specific files when relevant. **Never** store knowledge in
`~/.claude/...` memory — it doesn't travel with this repo.

| File | What goes in it | Who reads it |
|---|---|---|
| `user.md` | Who the user is, how they like to work, communication preferences | Zed |
| `lessons.md` | Corrections and confirmed approaches about how Zed itself should operate | Zed |
| `mention.md` | Product/architecture: apps, repos, branches, how repos relate | Zed (for briefs) |
| `conventions.md` | Engineering rules: branches, commits, PRs, QA, outward comms, tools | Zed + every worker |
| `people.md` | Team members, reviewers, account handles | Zed + workers raising PRs |
| `repos/<repo>.md` | Practical how-to per repo: setup, running, test logins, QA paths, gotchas | Workers on that repo |
| `../config/projects.json` | Machine-readable repo list: paths, hosts, base branches | `bin/zed` |

## Rules for keeping it good
1. **Learn once.** When the user teaches, corrects or decides something durable, write it to the
   right file in the same turn, then commit. Before asking the user anything, check here first.
2. **One home per fact.** Put each fact in exactly one file; link to it from elsewhere instead of
   copying. If a fact changes, edit it in place — don't append a contradicting line.
3. **Date volatile facts** ("as of 2026-10-26: releases/v4.44.1") so staleness is visible.
4. **Separate what was told from what was inferred.** Mark inferences as `(inferred, unconfirmed)`
   and never brief a worker with an inference as if it were a decision.
5. **Harvest from workers.** Workers report `Learnings:` in ZED:DONE (setup steps, logins, flaky
   tests, gotchas). Fold them into `repos/<repo>.md` after each task.
6. **Prune.** If a file grows past ~200 lines or mixes topics, split it and update this index.
