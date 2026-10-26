# Mention — what Zed knows

_Source: the user, 2026-10-26. Update when the user says otherwise._

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
  - `cm_db` — DB procedures used by cm-api (Bitbucket; default branch `master`).
  - `cm-lib` — business rules that cm-api needs (Python; Bitbucket `criticalmedianyc/cm_lib`,
    default `master`). Note: the clone lives at `cm-lib/master/`, not `cm-lib/`.
- **Shared**
  - `cm-reports` — reports module (Vue 2-compatible components), shared by CM and 360.
    Base branch for new work depends on the consumer: CM → `main`, 360 → `medusa`.
    - `main` → linked into `cm-fe` to provide CM's reports.
    - `medusa` → consumed by 360; cm-reports builds medusa's dist files and pushes them into
      `onclusive-core-client`.
- **Unification**
  - `unified-fe` — microfrontend shell bringing CM and 360 together. Base branch `main`.
  - `unified-mention/` — plans for truly merging into one app: one frontend repo, one backend
    repo, one DB.

## Other repos / folders
- `claim` — CLAIM (Client, Account and Identity Management): Fastify/TS/Prisma service for managing
  Auth0 users across Onclusive apps. GitHub `AirPR/claim`, default `main`. Has its own CLAUDE.md.
- `gum` — GUM (Global User Management): predecessor/sibling of CLAIM, same stack. GitHub `AirPR/gum`,
  default `main`. (Relationship to CLAIM inferred, unconfirmed.)
- `iac` — not a git repo; the user's research notes/configs on 360's current infrastructure (Porter),
  made for writing a disaster recovery plan.
- `prompt-library` — the user's project to build an agentic engineering workflow (Claude Code /
  Copilot instructions and skills) to scale across the team. GitHub `skpstg/prompt-library`, default `main`.


Conventions (branches, commits, PRs, QA): see `conventions.md`.
