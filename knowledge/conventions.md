# Engineering conventions (workers: follow all of this)

_Source of truth for how work is done on Mention. Set by the user; don't deviate without asking._

## Branches
- `<worktype>/<jira-id-lowercase>-<short-description>`, e.g. `story/ment-123-digest-ai-disclosure`.
- Worktype comes from the Jira issue type (Story → `story`, Bug → `bug`, Task → `task`, …).
- Zed creates your branch and worktree; stay on it.

## Commits
- `JIRA-ID | Description in an affirmative, positive, imperative sentence`
  e.g. `MENT-123 | Add AI Disclosure Label to Digest Summary`.
- Commit only on your own branch.

## Never
- Push to, commit to, or rebase base/release/deploy branches (`main`, `deploy`, `velocity-build`,
  `velocity-deploy`, `medusa`, `master`, `releases/*`, …).
- Force-push. Merge PRs.

## Before committing / raising a PR
1. **Browser QA like a tester.** Use `playwright-cli` (preferred; https://playwright.dev/agent-cli/introduction)
   or `agent-browser` as fallback — both are installed. Check `repos/<repo>.md` for how to run
   and log in; if it isn't documented and you can't work it out from the repo, ask Zed.
   Cover the happy path, the ticket's edge cases and nearby regressions. Record what you verified.
2. **Self-review** for clean, readable, long-term-maintainable code; follow existing patterns;
   no debug leftovers or dead code; tests where the repo has them. Run lint, type check, tests.

## Pull requests
- Title: `JIRA-ID | Description`, e.g. `MENT-123 | Add AI Disclosure for Digest`.
- Target: the base branch from your brief.
- Description: what and why, Jira link, how it was QA'd (with what), anything reviewers should focus on.
- Open it as **draft**. When **all CI checks pass and the work is complete**, mark it ready for
  review and request the reviewers listed in `people.md`.
- GitHub repos: `gh pr create --draft ...`, `gh pr checks`, `gh pr ready`, `gh pr edit --add-reviewer`.
- Bitbucket repos (cm_api, cm_db, cm_lib): `<ZED_HOME>/bin/bb pr create --draft ...`
  (`bin/bb --help`). If auth isn't set up, report ZED:BLOCKED.

## Outward communication
Raising your own PR is fine. Everything else others will see — Jira/Confluence edits or comments,
Slack, email, PR comments — goes to Zed as a draft in a ZED:QUESTION for the user's approval.
