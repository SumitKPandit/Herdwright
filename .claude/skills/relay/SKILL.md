---
name: relay
description: Pass the user's answers or new instructions to the right worker session and record them.
---

# Relay

1. Map each part of the user's message to a Q-id or task. If it's ambiguous which question an
   answer belongs to, ask the user — don't guess.
2. `bin/zed q answer <Q> "<answer, verbatim>"`.
3. Before prompting, `herdr agent get <agent>`: it must be `idle` or `done`. If it is `working`,
   wait for the watcher. If `blocked`, it's an approval UI — handle with send-keys per the user's decision.
4. `herdr agent prompt <agent> "Answer from the engineering manager to your question <n> (<short restatement>): <verbatim answer>. Continue."`
   Combine all answers for one worker into one prompt.
5. Also append the decision to the worker's brief under "Decisions already made by the user"
   so it survives if the session is restarted. Commit the Zed repo.
