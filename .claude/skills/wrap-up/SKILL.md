---
name: wrap-up
description: Decide whether a worker session/worktree can be closed and close it cleanly; mark tasks done.
---

# Wrap up

A session can be closed when **all** hold:
- its work is published (branch pushed and/or PR raised) — `git -C <worktree> status` clean and no unpushed commits;
- no open questions for it (`bin/zed q list`);
- nobody is waiting for it to act (e.g. PR review fixes requested → keep it, or respawn later).

Then `bin/zed close <S>` (refuses if the worktree has unpushed/uncommitted work; never pass
`force` without the user's OK). Tell the user it was closed and where to resume from (PR/branch).

Mark the task: `pr_open` while PRs await review/merge; `done` only when the user says it's done
or the PR is merged (check `gh pr view <url> --json state`). Commit the Zed repo.

Parking: if the user pauses a task with unpublished work, ask whether to push a WIP branch;
otherwise keep the worktree (`bin/zed close <S> keep-worktree`) and set task status `parked`.
