# Zed status

_Generated 2026-10-26 08:45 from db/zed.db. Do not edit by hand._

## Waiting on you

- Nothing.

## In progress

### T1 · no-jira · Unified Mention discovery & planning (user's own session)
- status: **active** · worktype: - · updated 2026-10-26 08:10
- User-started Claude session in ~/codebase/mention/unified-mention: discovery docs, Figma links, Confluence 'Unified Mention – Discovery' folder
- S1 (external) `wC:p1` unified-mention `-` ← `-` · running / agent idle
  - 2026-10-26 08:10 [created] Unified Mention discovery & planning (user's own session)
  - 2026-10-26 08:10 [adopt] S1 adopted external session in wC:p1 (/Users/sumitkumarpandit/codebase/mention/unified-mention)

### T2 · no-jira · Split Zed into shareable core (main) and per-user profile (branch)
- status: **active** · worktype: chore · updated 2026-10-26 08:45
- User wants anyone to clone Zed and onboard; Sumit's work lives on a separate branch
- S2 `t2-zed` zed `chore/zed-core-profile-split` ← `main` · running / agent working
  - 2026-10-26 08:37 [spawn] S2 t2-zed on zed chore/zed-core-profile-split ← main (/Users/sumitkumarpandit/codebase/mention/.worktrees/zed/zed-core-profile-split)
  - 2026-10-26 08:42 [plan] plan after worker's ZED:DONE: review branch, merge into local main, run bin/zed-migrate-profile on live checkout, create profile/sumit, make main core-only, push main + (with user OK if blocked) profile branch. Local commits with reviewer UUIDs are NOT pushed yet — they belong on profile/sumit.
  - 2026-10-26 08:45 [decision] decision: add single-active-Zed lock (read-only / take-over) to T2 scope (user, 2026-10-26)

## PR open

- None.

## Not started / parked

- None.

## Recently finished

- None.
