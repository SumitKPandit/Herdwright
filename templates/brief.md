# Brief — T{{TASK}} · {{TICKET_ID}} · {{TITLE}}

## Goal
<FILL: what the user wants, in their words where possible>

## Context
- Repo: {{REPO}} ({{ROLE}})
- Worktree: {{WORKTREE}}   Branch: {{BRANCH}}   Base / PR target: {{BASE}}
- Ticket: {{TICKET_LINE}}
- Related repos / sessions: <FILL: other repos or sessions this work depends on or feeds into, or "none">

## Decisions already made by the user
- <FILL: each confirmed decision, verbatim, or "none yet">

## Known open points (do not assume — ask if they matter)
- <FILL: what is still unknown or ambiguous, or "none known">

## QA
- How: browser QA with the tool named in the user's rules for UI work; otherwise exercise the work for real (run it, call it).
- Environment / how to log in: {{QA_ENV}}

## Plan
- Before any other work, write `{{PLAN_FILE}}`: a checklist
  (`- [ ] step — done when …`) covering the whole task, in order, with a rough time per step.
  Steps already finished by an earlier session start as `- [x]`. One line per step (no "1-5 done" ticks); only
  merge / push / raise-PR steps may stay open when you hand over to review and QA (start their title with the
  action, or put `[after-review]` in it). End the plan with the fixed line
  `- [ ] Run zed-employee finish and report (ZED:DONE)`: `zed-employee finish` ticks exactly that line itself.
- Follow it. Tick each step as soon as it's done. Change the plan only for a concrete reason, and
  record the reason in the file. Zed tracks progress from this file.

## Definition of done
- <FILL: acceptance criteria>
- QA done, self-review done, committed and PR raised per the user's rules against {{BASE}}.
- Finish with a ZED:DONE block.
