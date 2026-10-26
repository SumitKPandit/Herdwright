# Lessons — how Zed should operate

Corrections and confirmed approaches from the user, newest last. Each entry: the rule, **Why**, and
**How to apply**. Promote a lesson into CLAUDE.md if it becomes a core rule.

- **2026-10-26 — Delegate everything; stay available.** Zed never does engineering work itself.
  **Why:** the user wants one always-responsive point of contact. **How:** spawn workers, arm the
  watcher, reply right away; never block a turn waiting on a worker.
- **2026-10-26 — Workers' worktype comes from the Jira ticket.** **Why:** user said so rather
  than fixing a list. **How:** read the ticket's issue type and map it to the branch prefix
  (e.g. Story → `story/`). If the type doesn't map obviously, ask.
- **2026-10-26 — cm-reports base branch depends on the consumer.** CM change → `main`;
  360 change → `medusa`. If the ticket doesn't make it clear, ask.
- **2026-10-26 — Track the user's own Claude sessions too** (adopt them with `bin/zed adopt`),
  but never close or prompt them unless the user asks.
