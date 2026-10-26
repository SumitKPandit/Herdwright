---
name: switch-runtime
description: Handle a usage limit on Claude Code or Copilot CLI — mark the runtime limited, tell the user, and on their OK hand paused workers over to the other runtime in the same worktree; also how Zed itself moves to the other runtime. Use when a watcher event says "possible <runtime> usage limit", a worker shows a limit message, or the user asks to switch.
---

# Switch runtime

## 1. Confirm and record the limit
1. Read the worker's screen (`herdr agent read <agent> --source visible`) and check that the line
   really is the CLI's limit message, not something the worker wrote about rate limits in code.
   Note the reset time if it shows one (Claude: "resets 3pm", "continuing automatically at …").
2. `bin/zed runtime limited <runtime> until="<reset time as shown>" <the message>`. From now on
   `spawn` skips that runtime until `bin/zed runtime ok <runtime>`. Claude and Copilot have
   separate limits, so one being limited says nothing about the other.

## 2. Tell the user — then wait
Say which runtime is limited, the reset time, and which sessions it paused (`bin/zed session list`,
the runtime column). For each paused worker, offer the choice:
- **wait** for the reset (Claude workers continue by themselves after it), or
- **hand over** to the other runtime now, in the same worktree and branch.

Never hand over without the user's OK (their decision, recorded in the profile's lessons/brief).
If the other runtime is also limited or not in the profile, say so; waiting is then the only option.

## 3. Hand over (after the user's OK)
`bin/zed handoff <S> [runtime=<rt>]` writes `profile/briefs/T<id>-<repo>-handoff-S<n>.md`. The note
holds the brief, the git state (status, commits, diff stat), the session's questions and answers,
and the last 150 lines of its screen. A limited worker can't write its own notes, so this is built
from what Zed can see. The command then:
1. closes the old session's herdr workspace and keeps the worktree, so the old worker can't resume
   on its own after the reset and race the new one;
2. spawns a new session on the other runtime in the same worktree and branch, with the brief and
   the handoff note.

Log it, tell the user the new session id, and keep watching. Uncommitted changes stay in the
worktree, and the new worker is told to check them before continuing.

## 4. When the limit resets
`bin/zed runtime ok <runtime>` (also check at the start of every session: `bin/zed runtime status`).
Sessions already moved stay where they are.

## 5. If Zed's own runtime is limited
A limited Zed can't act, so this depends on the user:
- As soon as workers on your runtime start reporting limits, warn the user that you may be
  limited soon too, and how to switch.
- To switch, the user opens the other CLI in the Zed folder inside herdr (`claude` or `copilot`;
  Copilot in interactive mode, never autopilot). That session sees the lock held by a live but
  limited session, offers **take over**, and runs `bin/zed lock acquire --take-over`. When the old
  session comes back after its reset, its watcher prints `LOCK LOST` and it stays read-only.
