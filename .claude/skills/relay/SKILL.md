---
name: relay
description: Pass the user's answers or new instructions to the right worker session and record them.
---

# Relay

1. Map each part of the user's message to a Q-id or task. If it's ambiguous which question an
   answer belongs to, ask the user — don't guess.
2. `bin/zed q answer <Q> "<answer, verbatim>"`.
2b. If the event that carried the question said the worker's context is large (`restart it fresh`), run
   `bin/zed handoff <S> restart` now: it writes a note with the git state and every question and answer
   (including this one), starts a fresh session in the same worktree, and that session continues from
   it. Skip steps 3-4 for that worker; the answer is already in its handoff note.
3. Prompt only through `bin/zed prompt <S> "<text>"`: it refuses a `working` session (a prompt mid-task can drop the rest
   of its plan); wait for the watcher, or add `--redirect` only when you mean to change what it is doing. If `blocked`,
   it's an approval UI — handle with send-keys per the user's decision.
4. `bin/zed prompt <S> "Answer from the user to your question <n> (<short restatement>): <verbatim answer>. Continue."`
   Combine all answers for one worker into one prompt.
4b. For the user's OK to **post** something (PR review/comment, ticket edit): save their words verbatim to a file and run
   `bin/zed relay-post <S> <words-file> --action "<exact action: repo, PR, text>"`. It sends their words plus the
   action so the auto-mode classifier sees the request. Never `handoff restart` between the OK and the posting.
5. Also append the decision to the worker's brief under "Decisions already made by the user"
   (`profile/briefs/`) so it survives if the session is restarted. If the answer is durable beyond
   this task, also record it per the `learn` skill. Commit the profile.
6. If the answer concerns a PR, check it with `bin/zed-pr state <url>` rather than by hand.
