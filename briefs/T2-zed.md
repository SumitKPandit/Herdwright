# Brief — T2 · Split Zed into shareable core and per-user profile

## Goal
Zed (this repo, an AI-assistant persona run by Claude Code) currently mixes **its capabilities**
with **one user's (Sumit's) personal work data**. Restructure it so that:
- **`main` branch = Zed core**: anything anyone can clone to get their own Zed. On first run,
  Zed onboards the new person: learns who they are, connects the accounts they need
  (GitHub, Bitbucket, Atlassian, Slack, … whatever they use), learns their rules and ways of
  working, and from then on is their assistant, learning as it goes.
- **A per-person branch** (e.g. `profile/sumit`) = core + that person's profile: their identity,
  rules, conventions, people, domain knowledge, repo notes, lessons, task DB, status, briefs.

User's words: "separate your capabilities from work you do for me… on the main branch remains
things people can clone and help you adapt to their workflow and on a separate branch everything
I ask you to do."

## Context
- Repo: `~/codebase/zed` (GitHub `skpstg/zed`). You are in a worktree of it on your own branch,
  based on `main`. Zed itself is live in the main checkout — **do not touch the main checkout, its
  `db/zed.db`, or any running herdr sessions/workspaces.**
- Read first: `README.md`, `CLAUDE.md`, `bin/zed`, `bin/bb`, `templates/*`, `.claude/skills/*`,
  `knowledge/*`, `config/projects.json`. Run `herdr --skill` to understand herdr (Zed drives worker
  Claude sessions through it).
- Today, personal data sits in: `knowledge/` (user.md, lessons.md, mention.md, conventions.md,
  people.md, repos/), `config/projects.json`, `db/zed.db`, `STATUS.md`, `briefs/`. Some text inside
  core files is also Sumit/Mention/Onclusive-specific (CLAUDE.md, worker-rules.md, skills, README,
  bin/zed paths like `~/codebase/mention`, bin/bb's hardcoded `criticalmedianyc/cm_api` in `check`).

## Decisions already made by the user
1. main = core capabilities anyone can clone; a separate branch holds everything personal to the user.
2. No git history rewrite.
3. Universal Zed traits (core, for everyone): delegate all work to workers while staying available;
   track everything; ask before any outward communication; learn once (never need to be taught
   twice); **strict no-assumption rule**; **browser QA before every PR**.
   Personal (Sumit's profile): commit/PR/branch formats, reviewers, Mention domain knowledge, repos,
   Bitbucket specifics, his preferences.
4. Zed must keep its knowledge organized and improve over interactions. Learning about a *person*
   goes to their profile; learning about *being a better assistant* (tool quirks, workflow
   improvements) goes to core so everyone benefits.

## Zed's design direction (implement; ask via ZED:QUESTION if you see a real problem with it)
- Fixed layout: core files at the repo root; everything personal under a single `profile/` dir.
  `main` has no `profile/`; it ships `profile.template/` (skeleton files with guidance comments).
  Suggested profile contents: `profile.md` (who, accounts, preferences), `rules.md` (their extra
  rules, appended to worker rules), `lessons.md`, `knowledge/` (domain, conventions, people, repos/),
  `config/projects.json`, `db/`, `STATUS.md`, `briefs/`.
- `bin/zed` and `bin/bb` read all personal paths/settings from `profile/` (allow an env override
  such as `ZED_PROFILE_DIR` so you can test against a throwaway profile). Nothing personal hardcoded
  in core code (code root, worktree root, Keychain service name, Bitbucket test repo → profile config).
- `spawn` gives workers core worker rules + the profile's `rules.md` (e.g. concatenate into one
  generated file) and points them at the profile's conventions and repo notes.
- Core `CLAUDE.md`: generic Zed identity + operating manual. Session start: if `profile/` is missing,
  run the onboarding skill; otherwise load the profile files.
- New skills: `onboarding` (first run: interview, create `profile/` from the template, create and
  switch to branch `profile/<name>`, connect accounts, verify each one), and `learn` (where a new fact goes,
  core vs profile, how to keep files tidy). Generalize the existing skills.
- A helper so core improvements made while running on a profile branch reach `main` safely,
  e.g. `bin/zed-core-sync` that cherry-picks commits touching only core paths onto `main` and
  refuses anything touching `profile/`; plus a guard (hook installed by a setup script) that
  rejects commits to `main` touching `profile/`.
- Generic core lessons already learned (put them in a core playbook file): macOS
  `security add-generic-password -w` interactive prompt truncates at 128 chars (use `-w "$(pbpaste)"`);
  Claude Code shows a folder-trust dialog on fresh worktrees (spawn auto-accepts it for worktrees
  Zed created); the auto-mode classifier may block pushes of third-party/employer data to personal
  remotes; `timeout` isn't on macOS.
- `README.md` written for a newcomer: what Zed is, prerequisites (Claude Code, herdr, git, gh,
  python3), clone, run `claude` in the repo inside herdr, onboarding happens automatically.
- **Do not migrate Sumit's data yourself.** Write `bin/zed-migrate-profile` (idempotent) that moves
  today's personal files into `profile/` in a checkout; Zed will run it on the live checkout and
  build the `profile/sumit` branch afterwards. Make sure today's db schema keeps working (bin/zed
  already migrates `sessions.external`).
- Remove Sumit/Mention/Onclusive-specific content from core files; anything removed that is still
  true for Sumit belongs in the migration's target (e.g. a `profile/rules.md` the migration writes
  with his commit/PR/branch conventions currently in `knowledge/conventions.md`).

## Known open points
- If something about personal-vs-core is ambiguous, ask.

## QA
This is a CLI/docs project, no browser UI. QA by actually exercising it with a throwaway profile
(`ZED_PROFILE_DIR` in your scratchpad): task add/log/q/status, spawn a real worker on a scratch git
repo (as Zed's original self-test did), watcher, close; run the migration script on a **copy** of
the main checkout (e.g. `cp -R`, outside the repo) and check `bin/zed status` there shows the existing tasks.
Clean up every herdr workspace and worktree you create.

## Definition of done
- Branch pushed to origin; **do not raise a PR and do not merge** — Zed will review and merge.
- Commit message format for this repo: `ZED | Description in imperative mood`.
- Finish with ZED:DONE, including exactly how Zed should run the migration and create `profile/sumit`.

## Added requirement (user, 2026-10-26): one active Zed per profile
Multiple Claude sessions may be opened in the Zed checkout. Guard against two acting as Zed:
- A lock (in the profile dir, gitignored) records the holder's herdr pane id, Claude session id, pid, and time.
- On startup Zed checks it. If another live holder exists, offer the user: **read-only** (answer
  status questions from the DB; no watcher, no relaying, no commits) or **take over** (acquire the
  lock; the old holder must notice and step down — e.g. its `bin/zed watch` exits with a clear
  line when it loses the lock, and CLAUDE.md tells Zed to stop acting then).
- Stale lock (holder pane no longer exists in herdr / pid dead) is cleared automatically.
- Mutating `bin/zed` commands and `watch` refuse (with a clear message) when the caller doesn't
  hold the lock; read-only commands (status, list, show) always work. Add a SQLite busy timeout.
- Different profiles never conflict.
