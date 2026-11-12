# Failure modes — how Zed lets its user down, and what stops it

Read before spawning, approving or reporting. **Enforced** guards are code and can't be skipped; **Practice** guards
depend on me. A new failure gets a row here and, where possible, a check in `bin/zed`.

| Failure | Guard |
|---|---|
| Worker re-tools, re-reads or redoes finished work | Enforced: plan file required at spawn; watcher `STALL` after 30 min without plan progress |
| Model or effort above what the work needs; too many parallel sessions | Enforced: `claude.json` effort ceiling and `effort_by_kind`; `max_running` counts every open Claude session and `spawn` queues the rest |
| A worker asks while still `working` | Enforced: workers append to a status file the watcher reads in any state; the watcher scans working screens for `ZED:` blocks; the answer waits for it (`zed-employee answers`) and is prompted once it is idle |
| A wake event is lost when my turn crashes | Enforced: events queue until `bin/zed ack`; `watch` re-shows them, `start` lists them |
| A worker idles without a `ZED:` block, waits on a permission prompt, or its pane is gone | Enforced: nudge once, wake me on a repeat; `blocked` reported again after 5 min; `gone` reported |
| A PR is merged, reviewed or failing and nobody notices | Enforced: the watcher polls open PRs |
| Watcher died | Enforced: Stop hooks re-arm it and block a turn that ends with tracked work and no watcher (OpenCode: one long-lived watcher; `start` and `spawn` restart it) |
| A DONE without a real QA line, an incomplete plan, or no `finish` | Enforced: the event flags it and `pr_open` isn't set; the worker Stop guard requires `zed-employee finish`. Practice: send it back |
| Review or QA before the build's plan is ticked; a second writer on a worktree | Enforced: `spawn`/`assign` refuse |
| A merge or PR without clean review and QA by non-authors | Enforced: `bin/zed merge`, `gate check`, git hooks |
| Closing a session with unpushed work | Enforced: the landed-work test (`bin/zed landed`, run by `close`) |
| Two Zeds act at once; lost lock ignored | Enforced: profile lock; `LOCK LOST` stops the watcher |
| Always-loaded files grow | Enforced: `start` prints the word count against the 4000 budget; prune or move content into a skill |
| Prompting a `working` worker derails it | Enforced: `bin/zed prompt` refuses without `--redirect` |
| I do the work myself, or read long screens into my context | Practice: delegate anything beyond bookkeeping; `tail`/`grep` what I need |
| A model does what a script could (publishing, checks, status) | Practice: before every brief list the deterministic steps and the access needed to script them |
| "Done" without proof (no PR, no QA, unpublished) | Practice: done only with evidence; otherwise send it back |
| Activity mistaken for delivery | Practice: workers publish each unit as soon as it is ready; judge by what the user can see, not files or ticks |
| Placeholder or stale content published; new conflicting sources after approval | Practice: completeness check before every publish; re-show the exact text and get fresh approval if sources changed |
| Worker guesses instead of asking; I decide what is the user's (product, spend, outward text) | Practice: no-assumption rule in briefs; ask with the question tool, recommendation first. I decide operational matters and report them |
| Parallel workers collide | Practice: disjoint ownership per worker |
| Auth expired (MCP, browser login, Keychain token) | Practice: surface the exact login step at once |
| A problem I see isn't raised (cost spike, limit near, stall) | Practice: raise it at once with a proposed fix |
| A correction fixed only in that one place | Practice: fix the cause in tooling, add a row here, write the lesson |
| Long, jargon-heavy replies | Practice: lead with what needs the user, plain words, short |
