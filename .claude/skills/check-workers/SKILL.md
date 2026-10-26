---
name: check-workers
description: Inspect worker sessions after a watcher event or when the user asks for status; classify each as question / done / blocked / still working and act.
---

# Check workers

1. `bin/zed session list` — live herdr state per session (`GONE` = pane/agent disappeared).
2. For each session that triggered an event (or all, for a full check):
   `herdr agent read <agent> --source recent-unwrapped --lines 150`
   (read more lines if the final block is cut off).
3. Classify by the last block:
   - `ZED:QUESTION` → for each question: `bin/zed q add <T> "<question>" session=<S>`; then ask
     the user (numbered, with Q-ids, trade-offs and the worker's recommendation).
   - `ZED:DONE` → `bin/zed log <T> "<summary>" kind=done`; if a PR is listed,
     `bin/zed session set <S> pr_url=<url>` and `bin/zed task set <T> status=pr_open`;
     `bin/zed session set <S> status=finished`. Tell the user. Consider `wrap-up`.
   - `ZED:BLOCKED` → `bin/zed q add` describing what's needed; ask the user.
   - herdr state `blocked` (approval/permission UI) → read the screen, describe exactly what it
     wants to do, ask the user. Answer with `herdr agent send-keys` only after the user decides.
     (Copilot workers ask through such dialogs whenever their permission reviewer isn't sure.)
   - The event says `possible <runtime> usage limit: …`, or the screen shows the CLI's limit
     message → `switch-runtime` skill.
   - No marker and idle → the worker paused without following protocol. Prompt it:
     "Continue, or end with ZED:QUESTION / ZED:DONE / ZED:BLOCKED." Don't bother the user.
   - `GONE` → find out (pane closed? crash?), tell the user, propose resuming with a new
     session on the same branch.
   - `ZED:DONE` with a PR but no real QA in its `QA:` line → send it back to QA first (CLAUDE.md, directive 6).
   Copilot sessions also record their AI credits used (`bin/zed status` shows them per session
   and per task); mention them when you report a Copilot task as done.
4. Commit the profile if anything changed.
