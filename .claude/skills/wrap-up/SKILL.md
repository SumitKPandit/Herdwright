---
name: wrap-up
description: Decide whether a worker session/worktree can be closed and close it cleanly; mark tasks done.
---

# Wrap up

A session can be closed when **all** hold:
- its work is published (branch pushed and/or PR raised) — `git -C <worktree> status` clean and no unpushed commits;
- no open questions for it (`bin/zed q list`);
- nobody is waiting for it to act (e.g. PR review fixes requested → keep it, or respawn later).

First harvest the worker's `Learnings:` (from its ZED:DONE) into `profile/knowledge/repos/<repo>.md`
following the `learn` skill. Then `bin/zed close <S>` (refuses if the worktree has unpushed/uncommitted work; never pass
`force` without the user's OK). Tell the user it was closed and where to resume from (PR/branch).

Mark the task: `pr_open` while PRs await review/merge; `done` only when the user says it's done
or the PR is merged (e.g. `gh pr view <url> --json state`). Commit the profile.

Closing or untracking a session is not delivery of its task. Check the original acceptance
criteria and record the actual output: for published documentation, its live URL and verified
content/rendering; for an assessment, the report and its acceptance, not an implemented solution.
Local drafts, a finished worker turn, or a handoff to another task must not be counted as a
completed user deliverable. Record transferred work against its continuing task explicitly.

Keep historical source verification, current artifact verification and user approval distinct.
Rewritten or split pages can lose context even when their source was verified: record checks
against the final pages and their citations, rather than inheriting verification from the source
material or from approval of the structure and style.

Parking: if the user pauses a task with unpublished work, ask whether to push a WIP branch;
otherwise keep the worktree (`bin/zed close <S> keep-worktree`) and set task status `parked`.
