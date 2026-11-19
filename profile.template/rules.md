# <Name>'s rules for workers

<!-- Appended to every worker's system prompt after the core worker rules (templates/worker-rules.md).
     Only what is specific to this user: formats, tools, reviewers, base branches, team habits.
     {{HW_HOME}} and {{PROFILE}} are replaced with absolute paths at spawn time. -->

## Branches
- <format, e.g. `<worktype>/<ticket-id>-<short-description>`; where worktype comes from>
- Herdwright creates your branch and worktree; stay on it.

## Commits
- <format, e.g. `TICKET-123 | Description in imperative mood`>

## Never
- <base / release / deploy branches by name, anything else off-limits>

## QA tools
- Browser QA: <tool, e.g. `playwright-cli`, fallback `agent-browser`>. How to run and log in per repo:
  `{{PROFILE}}/knowledge/repos/<repo>.md`; if it isn't documented and you can't work it out, ask Herdwright.

## Pull requests
- Title: <format>. Target: the base branch from your brief.
- Description: <what goes in it>.
- <draft first? when to mark ready? reviewers: see `{{PROFILE}}/knowledge/people.md`>
- <commands, e.g. `gh pr create --draft`>
