---
name: inbox
description: Handle feedback the user left on the dashboard (an `INBOX #<id>` watcher event, or `zed inbox` lists items) — answers, comments, ratings, task requests.
---

# Inbox

The dashboard (`bin/zed dashboard`) only writes the inbox; I act on it. Never skip an item.
`bin/zed inbox` lists what is unhandled. For each item, then `bin/zed inbox done <id> "<what I did>"`
(the note shows on the dashboard; needs the lock), then `bin/zed ack` the watcher event.

- **answer** (to an open question): treat it as the user's reply to that Q-id: follow the `relay` skill
  (`q answer`, prompt the worker verbatim). An answer approving an outward draft is the user's OK to send it.
- **comment** on a task: relay it to that task's running worker (`relay`); if none runs, say so in the note.
  A comment that changes scope or requirements is the user's decision: pass it on verbatim, don't interpret it.
- **rating**: `good` needs only the note. `not good` + reason is a correction: use the `learn` skill.
- **task** (new task request): follow `new-task`, using the title, ticket and repo given; ask the user for
  anything missing rather than guessing.
