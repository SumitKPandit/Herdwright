# Brief — T<task-id> S<session-id> · <ticket-id or "no ticket"> · <title>

## Goal
<what the user wants, in their words where possible>

## Context
- Repo: <repo> (<role>)
- Worktree: <path>   Branch: <branch>   Base / PR target: <base>
- Ticket: <ticket-id> — read it for full details (<tool that gives access, e.g. an MCP server>)
- Related repos / sessions: <other repos or sessions this work depends on or feeds into>

## Decisions already made by the user
- <each confirmed decision, verbatim>

## Known open points (do not assume — ask if they matter)
- <...>

## QA
- How: <browser tool from the user's rules for UI work; otherwise how to exercise it>
- Environment / how to log in: <from the profile's knowledge/repos/<repo>.md, or "ask Zed if unclear">

## Plan
- Before any other work, write `<profile>/briefs/T<task-id>-<repo>-plan.md`: a checklist
  (`- [ ] step — done when …`) covering the whole task, in order, with a rough time per step.
  Steps already finished by an earlier session start as `- [x]`.
- Follow it. Tick each step as soon as it's done. Change the plan only for a concrete reason, and
  record the reason in the file. Zed tracks progress from this file.

## Definition of done
- <acceptance criteria>
- QA done, self-review done, committed and PR raised per the user's rules against <base>.
- Finish with a ZED:DONE block.
