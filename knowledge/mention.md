# Mention — what Zed knows

_Source: the user, 2026-10-26. Update when the user says otherwise._

## The user
Engineering manager of **Mention** (Onclusive). Wants oversight of all team-lead, EM, product and
business decisions; workers handle engineering mechanics themselves.

## Apps
Mention currently consists of two apps:

- **360** — serves EU customers.
  - `onclusive-core-client` — frontend.
    - `main` → staging; base branch for new dev.
    - `deploy` → production.
    - `feat/mfe-unified-shell` → work integrating 360 into unified-fe.
  - `green-lantern` — backend.
    - `velocity-build` → staging; base branch for new dev.
    - `velocity-deploy` → production.
- **Critical Mention (CM)** — serves US customers.
  - `cm-fe` — frontend. Working base branch is `releases/vX.Y.Z`
    (as of 2026-10-26: `releases/v4.44.1`). Confirm the current one from recent PRs.
  - `cm-api` — backend (Bitbucket). Same convention (as of 2026-10-26: `releases/v6.2.1`).
  - `cm_db` — DB procedures used by cm-api (Bitbucket).
- **Shared**
  - `cm-reports` — reports module, shared by CM and 360.
    - `main` → linked into `cm-fe` to provide CM's reports.
    - `medusa` → consumed by 360; cm-reports builds medusa's dist files and pushes them into
      `onclusive-core-client`.
- **Unification**
  - `unified-fe` — microfrontend shell bringing CM and 360 together. Base branch `main`.
  - `unified-mention/` — plans for truly merging into one app: one frontend repo, one backend
    repo, one DB.

## Other folders in ~/codebase/mention (purpose not yet confirmed by the user)
`claim`, `gum`, `iac`, `cm-lib`, `prompt-library`.

## Conventions
- Jira project: MENT. Branch: `<worktype>/<jira-id-lower>-<short-desc>`,
  e.g. `story/ment-123-digest-ai-disclosure`.
- Commit: `MENT-123 | Add AI Disclosure Label to Digest Summary` (affirmative, imperative).
- Before commit/PR: browser QA + self-review for clean, readable, maintainable code.
- Workers may raise PRs. Any other outward communication (Jira, Confluence, Slack, comments)
  needs the user's approval first.
