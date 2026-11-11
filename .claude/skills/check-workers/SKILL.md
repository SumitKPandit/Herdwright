---
name: check-workers
description: Inspect worker sessions after a watcher event or when the user asks for status; classify each as question / done / blocked / still working and act.
---

# Check workers

The watcher already does the routine work in code: it reads each worker's status file and `ZED:` block,
records questions (`q add`), PR URLs, session/task status and the done-log itself, answers
allow-listed permission prompts, nudges a worker that went idle without a block (waking me only on a
repeat), and flags a usage limit and wakes me once. Each event it prints is
`[E<id>] S<s> T<t> <agent> …` followed by the extracted text (Q-ids, PR, QA). So:

1. **Read the event text once and trust it.** Don't re-read the worker's screen for what the event
   already contains. Un-acked events from an earlier crashed turn are shown again (`bin/zed start`
   lists them all).
2. **Act on each kind:**
   - `ZED:QUESTION` / `ZED:BLOCKED` (Q-ids already recorded) → ask the user: numbered, with the Q-ids,
     the worker's trade-offs and recommendation, verbatim.
   - `ZED:DONE` → tell the user; consider `wrap-up`. A `⚠ PR with no real QA` line means send it back to
     QA first (the manual, directive 6); the task is not set to `pr_open` until QA is real. Whenever you send
     a worker's work back, log it: `bin/zed log T<id> "S<id> <why>" kind=sendback`
     (a `ready-check` send-back says `ready-check` in the text).
   - `USAGE LIMIT` → tell the user; the worker resumes by itself after the reset (check that it did).
   - `PROTOCOL LAPSE again` → read the worker's screen (`herdr agent read <agent> --source recent-unwrapped
     --lines 150`) and decide: re-prompt, ask the user, or treat as gone.
   - `STALL` / `USAGE without plan progress` / `still blocked` → `bin/zed-crew state <S>` (branch, dirty and
     unpushed counts, diffstat, PR, plan progress) and `bin/zed-plan progress T<id>` (exit 1 = a step ran past
     twice its estimate); use `bin/zed-crew peek <S>` only if those facts are not enough, then redirect the worker.
   - a worker's first event, or a plan you haven't checked → `bin/zed-plan lint T<id>`; send a plan with
     problems back before it goes further.
   - a PR URL → `bin/zed-pr state <url>` instead of reading the PR by hand. Before accepting a PR as ready
     for review: `bin/zed-pr ready-check <url>`; send the worker back if it lists problems.
   - when the DB and reality may disagree → `bin/zed-crew reconcile` (also run by `bin/zed start`).
   - a `blocked` prompt the watcher left to me → read it with `--source visible`. If the user or the
     profile already authorizes this routine, in-scope operation, answer with `herdr agent send-keys`
     (a one-time approval, never a remembered blanket permission) and verify. Otherwise describe exactly
     what it wants and ask the user. Never infer approval for new scope, outward communication,
     destructive actions or permission changes.
   - a native compaction pause → verify a fresh native completion event and reduced context, not
     merely `idle`. The watcher can queue its routine `Continue` nudge during compaction and the worker
     may resume when it ends. Inspect current state before sending a resume: if already working,
     do not duplicate it; if blocked, inspect the exact UI as above. Treat nudge suppression as an
     automation requirement, not an active guarantee.
   - `GONE` → find out (pane closed? crash?), tell the user, propose resuming with a new session on the
     same branch.
3. **Ack what you handled:** `bin/zed ack E<id> …` (or `--all`). An un-acked event is shown again.
4. Commit the profile if anything changed.
5. The watcher is kept running as the manual's "Runtime differences" say for this install's runtime.
   One watcher at a time; a second exits with `WATCH_ALREADY_RUNNING`.
