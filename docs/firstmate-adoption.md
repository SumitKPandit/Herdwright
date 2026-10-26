# Adopting firstmate's scripts into Zed

Source: firstmate `bin/`: the 234 scripts in the T16 inventory plus the 13 files in `bin/backends/` (not in the inventory), 247 rows in all. Read as data at
`.ext/firstmate`; nothing was run or installed. Verdicts: **adopt** (port nearly as-is), **adapt** (port the logic to Zed's
model), **covered** (Zed has it, or S21's branch `chore/firstmate-efficiency` is building it), **skip** (does not fit Zed, with the reason).
Rules applied to every row: scripts first and model for judgment; no autonomous merges; outward communication needs the user's OK;
QA before PRs; workers' questions relayed faithfully; no new external tool, account or credential.

New scripts live in `bin/zed-<name>` with shared helpers in `bin/lib/zedlib.py`, so they merge cleanly beside S21's edits to `bin/zed`.

## Summary

| verdict | scripts |
|---|---|
| adopt | 2 |
| adapt | 29 |
| covered | 53 |
| skip | 163 |
| **total** | **247** |

## Ranked capabilities (adopt / adapt)

Ranked by model turns saved per day. Savings are **estimates** from reading the scripts and Zed's flow, not measurements.

| # | Capability | What it does | Why this rank | Saving (estimate) |
|---|---|---|---|---|
| 1 | zed-pr | `state <pr>` (CI, draft, conflicts, reviews, one verdict line), `wait <pr>` (one bounded blocking command until CI settles), `ready-check <pr>` (Zed's PR rules) | Every PR waits on CI. Today a worker polls (sleep-then-check turns that resend its whole context) and Zed re-checks in `check-workers`. One blocking command is 1 turn instead of ~5–15 per PR; `ready-check` replaces Zed's manual rule check | ~8–25 worker turns per PR; ~2 Zed turns per PR |
| 2 | zed-crew | `state <S>` / `all`: herdr state, branch, dirty/unpushed, last commit, diffstat vs base, plan progress, PR; `reconcile`: DB-vs-reality mismatches; `peek <S> [n]` bounded screen tail | `check-workers` reads ~150 screen lines and runs 3–6 git/herdr commands per worker per event. One JSON call replaces them; `reconcile` finds finished-but-unpushed, pane-gone, merged-PR-but-open-task without a turn | ~4–6 Zed tool calls and ~2–4k tokens per worker event |
| 3 | zed-plan | `lint <plan>` (each step has done-when + estimate), `progress <plan>` (done/total, overdue steps, elapsed) | The first thing a worker produces. Zed reads the plan by eye today and judges 'over estimate' by hand; this makes both exact | ~1–2 Zed turns per worker, plus earlier off-plan detection |
| 4 | zed-guard | `install <worktree>` (pre-push refuses protected branches + force-push; commit-msg checks `JIRA-ID \| Description`), `audit` (tangle, stale locks, worker on a protected branch) | Enforces the user's 'Never push to base/release/deploy, never force-push' and commit-format rules in git instead of in prompts. Saves review rework and the cost of one bad push, not tokens per event | rework avoided; ~1 turn per violation |
| 5 | zed-hygiene | report (orphan worktrees, merged/gone branches, clones behind origin, panes of closed sessions); `--apply` = fetch --prune + safe ff only | Session start and wrap-up currently do these checks by hand when remembered | ~3–5 turns per wrap-up/start; stops worktree sprawl |
| 6 | zed-hostload | `check [--json]`: memory pressure, swap, top agent RSS; exit code is a spawn go/no-go | Up to 6 agents on one Mac; thrash stalls every worker at once. Complements S21's max_running, which counts workers, not memory | avoided stalls, not tokens |
| 7 | zed-pretool | PreToolUse hook script: denies Edit/Write/`git commit`/`cd` into project repos for the coordinator session only (gated on holding the profile lock) | Turns prime directive 1 (a *Practice* guard today) into an *Enforced* one. Wiring in `.claude/settings.json` is Zed's step | avoided coordinator mistakes |
| 8 | zed-update | `check`: core commits on `main` not yet merged; `apply`: merge if clean | Replaces a manual fetch/compare/merge | ~2 turns per update |
| 9 | zed-export | `export <file>`: versioned JSON dump of the DB (tasks, sessions, questions, events) | Backup/portability of the only record Zed has; no token saving | data safety |

`bin/lib/zedlib.py` (profile paths, bounded run, line cap, git/herdr wrappers) is built first because every capability shares it.

## Not built here

- **S21 owns:** watcher/routine events, wake queue with ack, Stop hook, `zed start`, brief scaffolding, `max_running`, effort by kind, triage, quota estimate, spend ledger, status file, context ceiling, landed-work test, PR polling. Rows marked `S21` below are *covered* by those.

## Wiring (Zed's step, after S21's branch merges)

Each script has `--help`, a test in `tests/`, and works without `bin/zed` changes. Run tests from `tests/`, e.g. `python3 -m unittest test_zed_pr`.

| Script | Wire it into | How |
|---|---|---|
| `zed-pr` | worker rules, `check-workers` | worker rules: *wait on CI with `bin/zed-pr wait <url>` (one command), never sleep-then-check*; `check-workers` / `relay`: `bin/zed-pr state <url>` instead of reading the PR by hand; before accepting a PR as ready: `bin/zed-pr ready-check <url>`. S21's PR polling can shell out to `zed-pr state --json` |
| `zed-crew` | `check-workers`, session start | `bin/zed-crew state` replaces the screen read + git commands; `bin/zed-crew reconcile` in `zed start`; `peek` when facts are not enough |
| `zed-plan` | `bin/zed spawn` / `check-workers` | spawn (or the first worker event) runs `zed-plan lint T<id>`; the watcher's STALL check can use `zed-plan progress` (overrun = 2x estimate) |
| `zed-guard` | `bin/zed spawn` | after creating the worktree: `bin/zed-guard install <worktree> --repo <repo> --base <base> (--jira <ID> \| --no-commit-format)`; `zed-guard audit` in `zed start` |
| `zed-hygiene` | wrap-up skill, session start | `bin/zed-hygiene report [--fetch]` at wrap-up; `--apply` only with the user's OK (it fast-forwards clones) |
| `zed-hostload` | `bin/zed spawn` | refuse/queue a spawn on exit 2, warn on exit 1 |
| `zed-pretool` | `.claude/settings.json` | `hooks.PreToolUse` matcher `*` running `bin/zed-pretool` (it only acts in the session that holds the lock); needs the user's OK, see the report's questions |
| `zed-update` | session start | `bin/zed-update check` prints core commits waiting; `apply` merges them |
| `zed-export` | wrap-up / cron | `bin/zed-export export profile/db/backup.json` |

## Every script

### Shared helpers (`bin/lib/zedlib.py`)

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-line-cap-lib.sh` | 51 | adopt | per-line cap with 'N more omitted' for digests; ported into bin/lib/zedlib.py |
| `fm-timeout-lib.sh` | 304 | adopt | one hard-bounded command runner (macOS has no `timeout`); ported as zedlib.run_bounded |

### `bin/zed-pr`

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-forge-detect.sh` | 63 | adapt | forge from origin remote; Zed has `host` in projects.json, remote detection is the fallback |
| `fm-pr-health.sh` | 344 | adapt | 'new actionable PR condition' rules become `zed-pr state` verdicts; the watcher-side poll stays S21's |
| `fm-pr-lib.sh` | 1584 | adapt | validated PR URL parsing and forge-neutral reads; ported for GitHub + Bitbucket only |
| `fm-pr-poll.sh` | 244 | adapt | worker-side blocking `zed-pr wait` (one command, bounded) from the poll loop; the watcher poll is S21's PR polling |
| `fm-pr-state.sh` | 175 | adapt | one-shot read of CI/review/draft/merge blockers on a PR; ported for gh and bb |

### `bin/zed-crew`

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-crew-state.sh` | 1400 | adapt | deterministic 'current state' of a worker from authoritative sources, no model; the model of zed-crew |
| `fm-fleet-snapshot.sh` | 2087 | adapt | structured snapshot of every worker; Zed's per-session facts, JSON, bounded |
| `fm-inactive-reconcile.sh` | 710 | adapt | reconcile suspicious inactive outcomes (finished with unpushed work, pane gone); DB-vs-reality check |
| `fm-peek.sh` | 46 | adapt | bounded tail of a worker's screen for cheap diagnosis; `zed-crew peek` |
| `fm-review-diff.sh` | 182 | adapt | diffstat of a worker branch against its base, without reading the diff in the model |

### `bin/zed-plan`

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-brief-heading-lib.sh` | 95 | adapt | heading reader pattern reused to parse the plan file's steps |

### `bin/zed-guard`

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-git-push-guard.sh` | 163 | adapt | per-worktree pre-push guard; Zed's version refuses protected branches and force-push (user rule 'Never') |
| `fm-guard.sh` | 292 | adapt | worktree-tangle half only; the watcher-liveness half is S21's Stop hook |
| `fm-lock-lib.sh` | 104 | adapt | proof a git lock file is abandoned; stale index.lock stalls a worker |
| `fm-tangle-lib.sh` | 53 | adapt | worktree-tangle check: a worker committing in the primary checkout instead of its worktree |

### `bin/zed-hygiene`

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-bootstrap.sh` | 1726 | adapt | detect + refresh half (clones behind origin, orphan worktrees); install half is covered by bin/zed-setup |
| `fm-ff-lib.sh` | 580 | adapt | the safe fast-forward rules (clean tree, on base, ff-only) reused by zed-hygiene and zed-update |
| `fm-fleet-sync.sh` | 465 | adapt | fetch + safe fast-forward of clones, prune merged branches; report-only unless --apply |
| `fm-herdr-session-cleanup.sh` | 337 | adapt | stale herdr panes of closed sessions, reported only; never touches panes Zed did not create |

### `bin/zed-hostload`

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-capacity.sh` | 740 | adapt | host-headroom half (is there room for another worker); lane counting is S21's max_running |
| `fm-jev-mem-guard.py` | 260 | adapt | RSS/swap thrash guard; ported to macOS (vm_stat, sysctl) with a spawn go/no-go |
| `fm-jev-mem-guard.sh` | 7 | adapt | RSS/swap thrash guard; ported to macOS (vm_stat, sysctl) with a spawn go/no-go |

### `bin/zed-pretool`

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-cd-command-policy.mjs` | 153 | adapt | command-policy rules for the cd-guard; logic reused in Python, no node dependency |
| `fm-cd-pretool-check.sh` | 190 | adapt | PreToolUse guard keeping the coordinator from doing the work itself (prime directive 1) |
| `fm-primary-scope-lib.sh` | 42 | adapt | 'act only in the genuine primary' predicate; Zed's equivalent is holding the profile lock |
| `fm-subagent-pretool-check.sh` | 207 | adapt | PreToolUse guard keeping the coordinator from doing the work itself (prime directive 1) |

### `bin/zed-update`

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-update.sh` | 246 | adapt | fast-forward to the latest origin; Zed's version reports/merges core commits from main |

### `bin/zed-export`

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-record-export.py` | 1179 | adapt | fixed, versioned export of private records; Zed's version dumps the SQLite DB to JSON |
| `fm-record-export.sh` | 84 | adapt | fixed, versioned export of private records; Zed's version dumps the SQLite DB to JSON |

### Covered by S21 (`chore/firstmate-efficiency`)

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-afk-return.sh` | 931 | covered | 'what happened while you were away' digest: S21 `zed start` + status file |
| `fm-arm-command-policy.mjs` | 973 | covered | watcher-arm policy exists only to enforce the arm step; S21's Stop hook removes the step |
| `fm-arm-pretool-check.sh` | 200 | covered | watcher-arm policy exists only to enforce the arm step; S21's Stop hook removes the step |
| `fm-bearings-snapshot.sh` | 740 | covered | compact bounded fleet digest: S21 status file / `start`; line cap comes from zedlib |
| `fm-brief.sh` | 823 | covered | brief scaffolding: S21 'brief scaffolding' |
| `fm-classify-lib.sh` | 2831 | covered | watcher, wake classification, routine-event handling: S21 'routine-event handling' |
| `fm-claude-stop-autoarm.sh` | 588 | covered | Stop hook: tokenless watcher re-arm + turn-end guard: S21 'Stop hook' |
| `fm-dispatch-resolve.sh` | 561 | covered | dispatch profile by task kind: S21 'effort by kind' + the profile's model policy |
| `fm-dod-lib.sh` | 895 | covered | landed-work reachability gate at teardown: S21 'landed-work test at close' |
| `fm-fleet-ledger.sh` | 234 | covered | per-task spend/activity ledger: S21 'spend ledger' |
| `fm-landed-lib.sh` | 79 | covered | 'Recently landed' rule: `zed status` already lists recently finished; landed test is S21's |
| `fm-pipeline-spend.sh` | 298 | covered | per-task spend/activity ledger: S21 'spend ledger' |
| `fm-pr-check.sh` | 307 | covered | records pr= and arms a merge poll: `session set pr_url` + S21 PR polling |
| `fm-procevent-quota.sh` | 328 | covered | quota-exhaustion event: S21 quota estimate + existing USAGE watcher events |
| `fm-project-capacity-lib.sh` | 229 | covered | per-project worker cap: S21 `max_running` |
| `fm-push-transition-lib.sh` | 174 | covered | watcher, wake classification, routine-event handling: S21 'routine-event handling' |
| `fm-quota-axi-lib.sh` | 189 | covered | plan-window headroom: S21 'quota estimate'; quota-axi is a third-party tool (see Questions) |
| `fm-quota-choose.sh` | 402 | covered | plan-window headroom: S21 'quota estimate'; quota-axi is a third-party tool (see Questions) |
| `fm-session-start.sh` | 1125 | covered | one-command session start: S21 `zed start` |
| `fm-sessionstart-nudge.sh` | 57 | covered | session-start digest delivery: S21 `zed start` |
| `fm-sessionstart-run.sh` | 155 | covered | session-start digest delivery: S21 `zed start` |
| `fm-startup-growth-check.sh` | 417 | covered | ceiling on always-loaded context: S21 'context ceiling' |
| `fm-startup-memory-budget-lib.sh` | 224 | covered | ceiling on always-loaded context: S21 'context ceiling' |
| `fm-startup-memory-budget.sh` | 94 | covered | ceiling on always-loaded context: S21 'context ceiling' |
| `fm-teardown.sh` | 4158 | covered | teardown with landed-work test: `zed close` + S21 landed-work test (stale-lock proof goes to zed-guard) |
| `fm-transition-lib.sh` | 171 | covered | watcher, wake classification, routine-event handling: S21 'routine-event handling' |
| `fm-turnend-guard.sh` | 549 | covered | Stop hook: tokenless watcher re-arm + turn-end guard: S21 'Stop hook' |
| `fm-usage-warner.sh` | 519 | covered | plan-window headroom: S21 'quota estimate'; quota-axi is a third-party tool (see Questions) |
| `fm-wake-drain.sh` | 1089 | covered | durable wake queue with ack: S21 'wake queue with ack' |
| `fm-wake-grant.sh` | 111 | covered | durable wake queue with ack: S21 'wake queue with ack' |
| `fm-wake-lib.sh` | 3073 | covered | durable wake queue with ack: S21 'wake queue with ack' |
| `fm-watch-arm.sh` | 864 | covered | watcher, wake classification, routine-event handling: S21 'routine-event handling' |
| `fm-watch-checkpoint.sh` | 213 | covered | watcher, wake classification, routine-event handling: S21 'routine-event handling' |
| `fm-watch.sh` | 4204 | covered | watcher, wake classification, routine-event handling: S21 'routine-event handling' |

### Covered by Zed today

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-agy-trust.sh` | 185 | covered | folder-trust dialog: `zed spawn` answers it via runtimes[].trust_keys; editing ~/.claude.json is not needed |
| `fm-approval-failover.sh` | 95 | covered | failover of a worker whose approvals/limits are exhausted: `zed handoff` + switch-runtime skill |
| `fm-busy-event.sh` | 257 | covered | busy/idle semantics: herdr `agent_status` already gives it for both runtimes |
| `fm-busy-lib.sh` | 1247 | covered | busy/idle semantics: herdr `agent_status` already gives it for both runtimes |
| `fm-captain-hold.sh` | 2203 | covered | tasks waiting on the user: `waiting_user` state + `q add/answer` |
| `fm-claude-trust.sh` | 593 | covered | folder-trust dialog: `zed spawn` answers it via runtimes[].trust_keys; editing ~/.claude.json is not needed |
| `fm-contributions.jq` | 155 | covered | published work per task: sessions.pr_url + zed-pr |
| `fm-contributions.sh` | 782 | covered | published work per task: sessions.pr_url + zed-pr |
| `fm-control-lib.sh` | 625 | covered | pause/resume/stop a worker: `zed close`, `zed handoff`, herdr send-keys |
| `fm-control.sh` | 1293 | covered | pause/resume/stop a worker: `zed close`, `zed handoff`, herdr send-keys |
| `fm-decision-hold.sh` | 232 | covered | tasks waiting on the user: `waiting_user` state + `q add/answer` |
| `fm-fleet-view.sh` | 96 | covered | human fleet view: `zed status` / STATUS.md |
| `fm-harness.sh` | 684 | covered | which agent CLI am I: `zed lock status` (you.runtime) |
| `fm-hold-reason-lib.sh` | 78 | covered | tasks waiting on the user: `waiting_user` state + `q add/answer` |
| `fm-lock.sh` | 354 | covered | one active Zed per profile: `zed lock` |
| `fm-send.sh` | 1233 | covered | steering a worker with a durable record: `prompt_worker` + the relay skill's DB record |
| `fm-session-lock-lib.sh` | 601 | covered | one active Zed per profile: `zed lock` |
| `fm-spawn.sh` | 7488 | covered | `zed spawn` (worktree, trust prompt, runtime/model policy, brief check); isolation assertion reused by zed-guard audit |
| `fm-task-inbox-lib.sh` | 564 | covered | steering a worker with a durable record: `prompt_worker` + the relay skill's DB record |

### Skipped — Pi supervision branch

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-branch-dispatch.mjs` | 131 | skip | second-LLM supervision branch/lease machinery; S21 'coordinator cheap triage' is Zed's simpler version |
| `fm-branch-outcome.sh` | 812 | skip | second-LLM supervision branch/lease machinery; S21 'coordinator cheap triage' is Zed's simpler version |
| `fm-branch-prompt.sh` | 173 | skip | second-LLM supervision branch/lease machinery; S21 'coordinator cheap triage' is Zed's simpler version |
| `fm-branch-report.sh` | 153 | skip | second-LLM supervision branch/lease machinery; S21 'coordinator cheap triage' is Zed's simpler version |
| `fm-host-mirror.sh` | 319 | skip | second-LLM supervision branch/lease machinery; S21 'coordinator cheap triage' is Zed's simpler version |
| `fm-lease-lib.sh` | 333 | skip | second-LLM supervision branch/lease machinery; S21 'coordinator cheap triage' is Zed's simpler version |
| `fm-lease.sh` | 211 | skip | second-LLM supervision branch/lease machinery; S21 'coordinator cheap triage' is Zed's simpler version |
| `fm-live-lab.sh` | 869 | skip | second-LLM supervision branch/lease machinery; S21 'coordinator cheap triage' is Zed's simpler version |
| `fm-supervision-engine-lib.sh` | 458 | skip | second-LLM supervision branch/lease machinery; S21 'coordinator cheap triage' is Zed's simpler version |
| `fm-supervision-handoff.sh` | 156 | skip | second-LLM supervision branch/lease machinery; S21 'coordinator cheap triage' is Zed's simpler version |
| `fm-supervision-host.sh` | 1276 | skip | second-LLM supervision branch/lease machinery; S21 'coordinator cheap triage' is Zed's simpler version |
| `fm-supervision-instructions.sh` | 303 | skip | second-LLM supervision branch/lease machinery; S21 'coordinator cheap triage' is Zed's simpler version |
| `fm-supervision-lib.sh` | 113 | skip | second-LLM supervision branch/lease machinery; S21 'coordinator cheap triage' is Zed's simpler version |

### Skipped — attribution

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-git-strip-ai-trailers.sh` | 386 | skip | strips AI co-author trailers; attribution handling is the user's/harness policy, not something to change silently |

### Skipped — autonomy / auto-merge

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-afk-contract.sh` | 707 | skip | acts for the user while away, incl. merging; breaks Zed's rules (no autonomous merges, outward needs OK) |
| `fm-afk-launch.sh` | 1004 | skip | acts for the user while away, incl. merging; breaks Zed's rules (no autonomous merges, outward needs OK) |
| `fm-afk-start.sh` | 199 | skip | acts for the user while away, incl. merging; breaks Zed's rules (no autonomous merges, outward needs OK) |
| `fm-merge-authority-lib.sh` | 192 | skip | acts for the user while away, incl. merging; breaks Zed's rules (no autonomous merges, outward needs OK) |
| `fm-merge-local.sh` | 146 | skip | acts for the user while away, incl. merging; breaks Zed's rules (no autonomous merges, outward needs OK) |
| `fm-merge-outcome-lib.sh` | 115 | skip | acts for the user while away, incl. merging; breaks Zed's rules (no autonomous merges, outward needs OK) |
| `fm-pr-merge.sh` | 1851 | skip | acts for the user while away, incl. merging; breaks Zed's rules (no autonomous merges, outward needs OK) |
| `fm-supervise-daemon.sh` | 2222 | skip | acts for the user while away, incl. merging; breaks Zed's rules (no autonomous merges, outward needs OK) |
| `fm-supervisor-target-lib.sh` | 128 | skip | acts for the user while away, incl. merging; breaks Zed's rules (no autonomous merges, outward needs OK) |

### Skipped — backlog (tasks-axi)

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-backlog-handoff.sh` | 1100 | skip | markdown backlog via tasks-axi; Zed's task record is the SQLite DB |
| `fm-backlog-receive.sh` | 188 | skip | markdown backlog via tasks-axi; Zed's task record is the SQLite DB |
| `fm-backlog-transition-lib.sh` | 1237 | skip | markdown backlog via tasks-axi; Zed's task record is the SQLite DB |
| `fm-tasks-axi-lib.sh` | 262 | skip | markdown backlog via tasks-axi; Zed's task record is the SQLite DB |
| `fm-tasks-axi.sh` | 154 | skip | markdown backlog via tasks-axi; Zed's task record is the SQLite DB |

### Skipped — cosmetic

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-herdr-groups.sh` | 540 | skip | pane grouping/attention marks; display only |
| `fm-rex-marks.sh` | 493 | skip | pane grouping/attention marks; display only |

### Skipped — firstmate CI/lab

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-doc-audience-check.sh` | 269 | skip | firstmate's own CI, lab homes and pinned tool installs |
| `fm-herdr-ci-cleanup.sh` | 115 | skip | firstmate's own CI, lab homes and pinned tool installs |
| `fm-herdr-lab-viewer.py` | 204 | skip | firstmate's own CI, lab homes and pinned tool installs |
| `fm-herdr-lab.sh` | 593 | skip | firstmate's own CI, lab homes and pinned tool installs |
| `fm-install-actionlint.sh` | 84 | skip | firstmate's own CI, lab homes and pinned tool installs |
| `fm-install-herdr.sh` | 96 | skip | firstmate's own CI, lab homes and pinned tool installs |
| `fm-install-shellcheck.sh` | 84 | skip | firstmate's own CI, lab homes and pinned tool installs |
| `fm-install-treehouse.sh` | 95 | skip | firstmate's own CI, lab homes and pinned tool installs |
| `fm-lab-home.sh` | 107 | skip | firstmate's own CI, lab homes and pinned tool installs |
| `fm-lint-workflows.sh` | 137 | skip | firstmate's own CI, lab homes and pinned tool installs |
| `fm-lint.sh` | 1408 | skip | firstmate's own CI, lab homes and pinned tool installs |
| `fm-test-isolation-proof.sh` | 594 | skip | firstmate's own CI, lab homes and pinned tool installs |
| `fm-test-run.sh` | 2817 | skip | firstmate's own CI, lab homes and pinned tool installs |

### Skipped — firstmate internals

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-env-lib.sh` | 31 | skip | firstmate-specific plumbing (trace context, operational-input markers, roles, startup network stage) |
| `fm-marker-lib.sh` | 12 | skip | firstmate-specific plumbing (trace context, operational-input markers, roles, startup network stage) |
| `fm-operational-input.sh` | 435 | skip | firstmate-specific plumbing (trace context, operational-input markers, roles, startup network stage) |
| `fm-path-lib.sh` | 39 | skip | firstmate-specific plumbing (trace context, operational-input markers, roles, startup network stage) |
| `fm-role-lib.sh` | 544 | skip | firstmate-specific plumbing (trace context, operational-input markers, roles, startup network stage) |
| `fm-role.sh` | 82 | skip | firstmate-specific plumbing (trace context, operational-input markers, roles, startup network stage) |
| `fm-startup-network.sh` | 759 | skip | firstmate-specific plumbing (trace context, operational-input markers, roles, startup network stage) |
| `fm-timing-lib.sh` | 179 | skip | firstmate-specific plumbing (trace context, operational-input markers, roles, startup network stage) |
| `fm-trace-context-lib.sh` | 227 | skip | firstmate-specific plumbing (trace context, operational-input markers, roles, startup network stage) |
| `fm-vendor-auth-probe.sh` | 190 | skip | firstmate-specific plumbing (trace context, operational-input markers, roles, startup network stage) |

### Skipped — forge=external

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-external-review-lib.sh` | 201 | skip | review rail for an external review tool; Zed repos use GitHub/Bitbucket PRs |
| `fm-review-check.sh` | 123 | skip | review rail for an external review tool; Zed repos use GitHub/Bitbucket PRs |

### Skipped — lavish board

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-bearings-board.sh` | 459 | skip | lavish interactive board; not a Zed surface |
| `fm-procevent-lavish.sh` | 978 | skip | lavish interactive board; not a Zed surface |

### Skipped — no-mistakes

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-gate-refuse-lib.sh` | 175 | skip | no-mistakes pipeline/gate integration; Zed does not use it |
| `fm-nm-run-lib.sh` | 658 | skip | no-mistakes pipeline/gate integration; Zed does not use it |

### Skipped — other harness/backend

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-agent-process-lib.sh` | 122 | skip | tmux/Orca/Rex backends and per-harness composer detection; Zed uses herdr only |
| `fm-backend-hometag-lib.sh` | 53 | skip | tmux/Orca/Rex backends and per-harness composer detection; Zed uses herdr only |
| `fm-backend.sh` | 1444 | skip | tmux/Orca/Rex backends and per-harness composer detection; Zed uses herdr only |
| `fm-commandcode-mod.ts` | 68 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-composer-lib.sh` | 2379 | skip | tmux/Orca/Rex backends and per-harness composer detection; Zed uses herdr only |
| `fm-cortex-plugin.sh` | 66 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-cursor-lib.sh` | 293 | skip | tmux/Orca/Rex backends and per-harness composer detection; Zed uses herdr only |
| `fm-devin-config.sh` | 59 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-exclude-tools-lib.sh` | 64 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-extension-launch-barrier.mjs` | 129 | skip | Pi extension binding; Zed has no Pi runtime |
| `fm-extension.mjs` | 2616 | skip | Pi extension binding; Zed has no Pi runtime |
| `fm-extension.sh` | 16 | skip | Pi extension binding; Zed has no Pi runtime |
| `fm-gemini-lib.sh` | 107 | skip | tmux/Orca/Rex backends and per-harness composer detection; Zed uses herdr only |
| `fm-hook-host-lib.sh` | 36 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-kimi-trust-lib.sh` | 89 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-kimi-turnend-hook.sh` | 276 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-omp-profile-lib.sh` | 81 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-polytoken-lib.sh` | 506 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-sessionstart-cursor.sh` | 40 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-t3-answer.sh` | 50 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-t3-mcp.mjs` | 1263 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-t3code-codex-env.sh` | 161 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-tmux-lib.sh` | 373 | skip | tmux/Orca/Rex backends and per-harness composer detection; Zed uses herdr only |
| `fm-turnend-guard-cursor.sh` | 441 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-turnend-guard-grok.sh` | 90 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `fm-worker-account-lib.sh` | 311 | skip | adapter for a harness Zed does not run (Kimi, Cursor, Grok, Devin, T3, Polytoken...); Zed runs Claude Code and Copilot CLI |
| `backends/cmux.sh` | 653 | skip | cmux session backend; Zed uses herdr |
| `backends/herdr-agent-view.py` | 128 | skip | firstmate's herdr agent-view projection; `herdr agent list/get` + `zed-crew` already give it |
| `backends/herdr-eventwait.py` | 157 | skip | push subscription to herdr's status-change stream; Zed's 3 s poll in `zed watch` costs no tokens, so push only trims latency (note for S21 if latency ever matters) |
| `backends/herdr-workspace-move.py` | 114 | skip | moves herdr workspaces between sessions; not needed |
| `backends/herdr.sh` | 4081 | skip | firstmate's herdr backend driver; `bin/zed` already drives herdr directly |
| `backends/orca.sh` | 346 | skip | Orca backend; Zed uses herdr |
| `backends/paseo.sh` | 670 | skip | Paseo backend; Zed uses herdr |
| `backends/rex-eventwait.lua` | 61 | skip | Rex backend event wait; Zed uses herdr |
| `backends/rex.sh` | 1451 | skip | Rex backend; Zed uses herdr |
| `backends/t3code.sh` | 617 | skip | T3 Code backend; Zed uses herdr |
| `backends/tern.sh` | 939 | skip | Tern backend; Zed uses herdr |
| `backends/tmux.sh` | 531 | skip | tmux backend; Zed uses herdr |
| `backends/zellij.sh` | 708 | skip | Zellij backend; Zed uses herdr |

### Skipped — process-event runner

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-check-lib.sh` | 74 | skip | custom watcher checks with trust binding; Zed has no custom-check plugin surface |
| `fm-check-register.sh` | 42 | skip | custom watcher checks with trust binding; Zed has no custom-check plugin surface |
| `fm-check-unregister.sh` | 52 | skip | custom watcher checks with trust binding; Zed has no custom-check plugin surface |
| `fm-procevent-extension-capture.pl` | 275 | skip | generic process-to-event runner; S21's watcher handles Zed's few event sources |
| `fm-procevent-lib.sh` | 1365 | skip | generic process-to-event runner; S21's watcher handles Zed's few event sources |
| `fm-procevent-remote-reply.sh` | 844 | skip | generic process-to-event runner; S21's watcher handles Zed's few event sources |
| `fm-procevent-when.sh` | 638 | skip | generic process-to-event runner; S21's watcher handles Zed's few event sources |
| `fm-procevent.sh` | 2570 | skip | generic process-to-event runner; S21's watcher handles Zed's few event sources |

### Skipped — repo conventions

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-ensure-agents-md.sh` | 269 | skip | rewrites project memory-file layout in worker repos; outside Zed's remit |

### Skipped — reviewer choice

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-pr-reviewers.sh` | 105 | skip | suggests reviewers from authorship; the user's rule is a fixed reviewer list (people.md), which zed-pr ready-check verifies instead |

### Skipped — secondmates/remote

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-config-inherit-lib.sh` | 1467 | skip | secondmate homes and parent/child channels; Zed has one coordinator |
| `fm-config-push.sh` | 264 | skip | secondmate homes and parent/child channels; Zed has one coordinator |
| `fm-home-seed.sh` | 1023 | skip | secondmate homes and parent/child channels; Zed has one coordinator |
| `fm-home-summary-refresh.sh` | 257 | skip | secondmate homes and parent/child channels; Zed has one coordinator |
| `fm-on.sh` | 125 | skip | secondmate homes and parent/child channels; Zed has one coordinator |
| `fm-parent-channel-lib.sh` | 175 | skip | secondmate homes and parent/child channels; Zed has one coordinator |
| `fm-pending-reply-lib.sh` | 1702 | skip | secondmate homes and parent/child channels; Zed has one coordinator |
| `fm-remote-delta-read.sh` | 231 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-doctor.sh` | 949 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-entrypoint.sh` | 193 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-file.sh` | 234 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-herdr-guard.sh` | 108 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-herdr-owner-lib.sh` | 176 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-home-provision.sh` | 283 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-home-seed.sh` | 272 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-inherit-push.sh` | 186 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-inherit.sh` | 192 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-job-lib.sh` | 1521 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-job-reap-orphans.sh` | 137 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-job-worker.sh` | 1349 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-readiness-lib.sh` | 44 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-secondmate-control.sh` | 457 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-remote-secondmate-relaunch.sh` | 95 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-secondmate-charter-lib.sh` | 44 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-secondmate-liveness-lib.sh` | 369 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-secondmate-nudge-lib.sh` | 106 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-secondmate-parent-lib.sh` | 70 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-secondmate-reconcile.sh` | 588 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-secondmate-registry-lib.sh` | 311 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-secondmate-report.sh` | 105 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-secondmate-restart-lib.sh` | 105 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-secondmate-restart.sh` | 396 | skip | secondmate / remote-host (SSH, launchd) machinery; Zed has one coordinator on one machine |
| `fm-stow-cascade.sh` | 251 | skip | secondmate homes and parent/child channels; Zed has one coordinator |

### Skipped — task model

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-project-mode.sh` | 263 | skip | firstmate's project registry/delivery posture (yolo); Zed's is profile/config/projects.json |
| `fm-project-origin-lib.sh` | 180 | skip | firstmate's project registry/delivery posture (yolo); Zed's is profile/config/projects.json |
| `fm-promote.sh` | 502 | skip | scout→ship promotion; Zed has no scout/ship contract split |

### Skipped — tooling updates

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-tool-update-check.sh` | 941 | skip | polls registries for tool updates; low value, adds network calls |

### Skipped — voice/X/mail

| script | lines | verdict | reason |
|---|---|---|---|
| `fm_voice_frame.py` | 166 | skip | voice relay; needs an external service and account |
| `fm_voice_records.py` | 597 | skip | voice relay; needs an external service and account |
| `fm-mail-check.sh` | 395 | skip | IMAP/SMTP mail plane: new credentials and outward sending; the user has Gmail via their own tools |
| `fm-mail.py` | 490 | skip | IMAP/SMTP mail plane: new credentials and outward sending; the user has Gmail via their own tools |
| `fm-mail.sh` | 650 | skip | IMAP/SMTP mail plane: new credentials and outward sending; the user has Gmail via their own tools |
| `fm-public-followup-collect.sh` | 125 | skip | X/Discord relay: external accounts and public posting |
| `fm-public-followup-emit.sh` | 419 | skip | X/Discord relay: external accounts and public posting |
| `fm-public-followup-lib.sh` | 599 | skip | X/Discord relay: external accounts and public posting |
| `fm-public-followup.sh` | 1681 | skip | X/Discord relay: external accounts and public posting |
| `fm-voice-client.py` | 1373 | skip | voice relay; needs an external service and account |
| `fm-voice-relay.py` | 1256 | skip | voice relay; needs an external service and account |
| `fm-x-dismiss.sh` | 122 | skip | X/Discord relay: external accounts and public posting |
| `fm-x-followup.sh` | 326 | skip | X/Discord relay: external accounts and public posting |
| `fm-x-lib.sh` | 1036 | skip | X/Discord relay: external accounts and public posting |
| `fm-x-link.sh` | 234 | skip | X/Discord relay: external accounts and public posting |
| `fm-x-poll.sh` | 209 | skip | X/Discord relay: external accounts and public posting |
| `fm-x-reply.sh` | 408 | skip | X/Discord relay: external accounts and public posting |

### Skipped — voice/chat relay

| script | lines | verdict | reason |
|---|---|---|---|
| `fm-inbox.sh` | 1159 | skip | out-of-band capture for a captain away from the terminal; Zed's user talks to Zed directly |
