---
name: relay
description: Pass the user's answers or new instructions to the right worker session and record them.
---

# Relay

1. Map each part of the user's message to a Q-id or task. If it's ambiguous which question an
   answer belongs to, ask the user — don't guess.
2. One command does the rest, in the right order: `bin/zed relay Q<id> "<answer, verbatim>" [Q<id> "<answer>" …]`.
   It prompts each worker once with all its answers, records them, and notes them in the session's brief under
   "Decisions already made by the user". It checks every worker first: one without a live agent refuses the whole
   command, and nothing is sent or recorded for any of them. A worker that is `working` is not interrupted (a prompt
   mid-task can drop the rest of its plan): its answers are recorded and noted, it reads them between steps with
   `zed-employee answers`, and the watcher prompts it with what it has not read once it is idle. Add `--redirect` only
   when you mean to change what it is doing now. If a prompt fails part-way, it says which questions are recorded and
   which are not: run it again for those only. If the worker is `blocked`, it's an approval UI: handle it with send-keys per the user's decision.
3. A new instruction that answers no recorded question goes through `bin/zed prompt <S> "<text>"`.
4. For the user's OK to **post** something (PR review/comment, ticket edit): save their words verbatim to a file and run
   `bin/zed relay-post <S> <words-file> --action "<exact action: repo, PR, text>"`. It sends their words plus the
   action so the auto-mode classifier sees the request.
5. If the answer is durable beyond this task, record it per the `learn` skill. Commit the profile.
6. If the answer concerns a PR, check it with `bin/zed-pr state <url>` rather than by hand.
