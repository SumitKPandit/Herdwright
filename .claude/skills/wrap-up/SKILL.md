---
name: wrap-up
description: Decide whether a worker session/worktree can be closed and close it cleanly; mark tasks done.
---

# Wrap up

A session can be closed when `bin/zed landed <S>` passes (exit 0). It is the script's landed-work test,
so don't re-derive it by hand: commits pushed, nothing uncommitted, its PR merged or closed (or no PR),
the task's plan complete, no open questions. If it fails it prints exactly what blocks closing; tell the
user, or fix it (e.g. a worker finishes the plan), rather than forcing. Also check that nobody is waiting
for the worker to act (e.g. PR review fixes requested → keep it, or respawn later).

First harvest the worker's `Learnings:` (from its ZED:DONE) into `profile/knowledge/repos/<repo>.md`
following the `learn` skill. Then `bin/zed close <S>` (runs the same test and refuses to remove a worktree that isn't landed; `keep-worktree`
closes just the session; never pass `force` without the user's OK). Tell the user it was closed and where to resume from (PR/branch).

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
