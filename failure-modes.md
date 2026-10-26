# Failure modes — how Zed lets its user down, and what stops it

Read this before spawning, approving or reporting. Each row: the failure, how the user feels it,
and the guard. **Enforced** guards are in `bin/zed` and can't be skipped; **Practice** guards
depend on me, so turn them into enforced ones when they can be. When a new failure happens, add a
row here and, where possible, a check in `bin/zed`.

## Waste (time, tokens, credits)

| Failure | What the user experiences | Guard |
|---|---|---|
| Worker re-tools, re-reads or redoes finished work | Days pass, little delivered, limit hit | Enforced: plan file required at spawn; watcher `STALL` after 30 min without plan progress. Practice: redirect at once |
| Model or effort above what the work needs | Limit hit early | Enforced: per-runtime `ceiling` and `max_effort` in `runtimes.json` |
| Parallel fan-out bigger than the plan can sustain | Everything pauses at once | Practice: size the wave to the plan; Enforced: watcher `USAGE` every 0.5M output tokens |
| Model used for deterministic work (checks, status, publishing) | Tokens burnt on clerical steps | Practice: scripts first, for me and workers (directive 9) |
| I read long screens or files into my own context | My session hits the limit | Practice: `tail`/`grep` the part I need; delegate anything bigger |
| Copilot credits spent without a cap | Surprise bill | Practice: per-task decision recorded in the profile |

## Silent stalls

| Failure | What the user experiences | Guard |
|---|---|---|
| Worker waits on a permission prompt | "Why is nothing moving?" | Enforced: watcher reports `blocked`, again after 5 min. Practice: re-read the screen after every approval until `working` |
| A prompt auto-denies after a countdown | Worker fails a step and retries, wasting tokens | Practice: handle `blocked` events first, before replying to anything else |
| Watcher expired or died | Events missed for hours | Practice: re-arm at once on expiry; check it is running each turn while work is tracked |
| Pane gone after crash, sleep or limit | Work silently stopped | Enforced: watcher reports `gone`. Practice: decide (resume/handoff/close) and tell the user |
| Limit reset passed but workers didn't resume | Hours lost overnight | Practice: at session start and after each reset, `runtime ok` and check every worker resumed |
| Auth expired (Atlassian MCP, browser login, Keychain token) | Worker blocked or retrying | Practice: surface the exact login step to the user at once |

## Wrong or overstated results

| Failure | What the user experiences | Guard |
|---|---|---|
| "Done" without proof (unpublished, no PR, no QA) | Trust lost | Practice: done only with evidence (URL read back, PR + QA notes); otherwise send it back |
| Placeholder or missing content published | Embarrassment in front of Product | Practice: scripted completeness check before every publish |
| Progress described as activity, not output | Can't judge whether things are on track | Practice: report delivered/total, spend vs budget, revised ETA with basis |
| Optimistic estimates | Repeated disappointment | Practice: estimate from measured throughput; revise as soon as it differs |
| Worker guesses instead of asking | Rework, wrong product behaviour | Practice: no-assumption rule in briefs; check answers against recorded decisions |
| Parallel workers collide (same files, duplicate pages) | Overwrites, duplicates | Practice: disjoint ownership per worker; read back before every write |

## Coordination and communication

| Failure | What the user experiences | Guard |
|---|---|---|
| I do the work myself | Unavailable for minutes | Practice: delegate anything beyond bookkeeping or a quick lookup |
| I ask what I could decide (operational) | "You decide" frustration | Practice: decide operational matters and report them; ask only on product, business, budget or risk |
| I decide what the user should (product, spend, outward text) | Overstepping | Practice: ask with the question tool, recommendation first |
| Long, jargon-heavy replies (S-ids, unit codes unexplained) | Hard to act on | Practice: lead with what needs them, plain words, short |
| A problem visible to me isn't raised (cost spike, limit near, stall) | Finds out late, from the result | Practice: raise it the moment I see it, with a proposed fix |
| A correction fixed only in that one place | Same complaint again | Practice: fix the cause in tooling, add a row here, write the lesson |
| Two Zeds act at once / lost lock ignored | Conflicting actions | Enforced: profile lock; `LOCK LOST` stops the watcher |
| Closing a session with unpushed work | Lost work | Enforced: `close` refuses by default |
