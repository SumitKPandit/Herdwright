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
- The plan file is `{{PLAN_FILE}}`. A plan session writes it and a build follows it; the build of a small task
  writes it itself before any other work. It lists the existing behaviour to protect with the test that pins each,
  one new test per requirement that must fail first, then a checklist
  (`- [ ] step — done when …`) covering the whole task, in order, with each step's tests and a rough time
  (the `plan-change` skill). Steps already finished by an earlier session stay `- [x]` and are not done again. One line per step (no "1-5 done" ticks); only
  merge / push / raise-PR steps may stay open when you hand over to review and QA (start their title with the
  action, or put `[after-review]` in it). End the plan with the fixed line
  `- [ ] Run hw-employee finish and report (HW:DONE)`: `hw-employee finish` ticks exactly that line itself.
- Follow it. Tick each step as soon as it's done, with where its evidence is. Change the plan only for a concrete reason, and
  record the reason in the file. Herdwright tracks progress from this file.

## Definition of done
- <FILL: acceptance criteria>
- QA done, self-review done, and <FILL: how the work is delivered, in the words of the user's rules (rules.md, Pull requests), e.g. "committed, pushed and a draft PR raised against {{BASE}}" or "committed on the branch; no push, no PR">.
- Finish with an HW:DONE block.
