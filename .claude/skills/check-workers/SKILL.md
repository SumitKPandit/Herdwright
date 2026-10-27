---
name: check-workers
description: Inspect worker sessions after a watcher event or when the user asks for status; classify each as question / done / blocked / still working and act.
---

# Check workers

The watcher already does the routine work in code: it reads each worker's status file and `ZED:` block,
records questions (`q add`), PR URLs, session/task status and the done-log itself, answers
allow-listed permission prompts, nudges a worker that went idle without a block (waking me only on a
repeat), and marks a runtime limited when a worker hits its usage limit. Each event it prints is
`[E<id>] S<s> T<t> <agent> …` followed by the extracted text (Q-ids, PR, QA). So:

1. **Read the event text once and trust it.** Don't re-read the worker's screen for what the event
   already contains. Un-acked events from an earlier crashed turn are shown again (`bin/zed start`
   lists them all).
2. **Act on each kind:**
   - `ZED:QUESTION` / `ZED:BLOCKED` (Q-ids already recorded) → ask the user: numbered, with the Q-ids,
     the worker's trade-offs and recommendation, verbatim.
   - `ZED:DONE` → tell the user; consider `wrap-up`. A `⚠ PR with no real QA` line means send it back to
     QA first (CLAUDE.md, directive 6); the task is not set to `pr_open` until QA is real.
   - `USAGE LIMIT … marked limited` → `switch-runtime` skill (the marking is already done).
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
   - `GONE` → find out (pane closed? crash?), tell the user, propose resuming with a new session on the
     same branch.
   Copilot sessions also record AI credits (`bin/zed status`); mention them when you report one done.
3. **Ack what you handled:** `bin/zed ack E<id> …` (or `--all`). An un-acked event is shown again.
4. Commit the profile if anything changed.
5. The Claude Stop hook re-arms the watcher after every turn (no model turn needed); if it is not
   installed or `bin/zed start` says `watcher: NOT RUNNING` with sessions running, run
   `bin/zed watch --once` in the background. Copilot has no hook: re-arm it by hand as CLAUDE.md says.
   One watcher at a time; a second exits with `WATCH_ALREADY_RUNNING`.
