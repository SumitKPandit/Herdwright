# Zed

Zed is a personal engineering assistant that runs on [Claude Code](https://claude.com/claude-code) or on [OpenCode](https://opencode.ai): an install uses one of them, for Zed and its workers alike.
You talk only to Zed. It takes your requests, writes a brief for each, and hands the work to
worker sessions running in [herdr](https://herdr.dev), one git worktree per session. It relays
their questions to you and your answers back, tracks every task, question and decision in a
small database, and asks you before anything goes out to other people.

What every Zed does, whoever uses it:
- **Delegates all the work and stays available.** Zed never codes itself; it's always free to talk.
- **Tracks everything.** Ask "what's in progress?" any time.
- **Asks before outward communication** — tickets, wiki, chat, email, PR comments.
- **Never assumes.** Zed and its workers ask instead of guessing.
- **QA before every PR**, in a real browser for anything with a UI.
- **Learns once.** Teach it something and it writes it down; you never repeat yourself.

## Get started

1. Install the prerequisites: Claude Code (`claude`), herdr, git, `gh` (logged in), python3.
2. Clone Zed: `git clone <this repo> ~/zed && cd ~/zed && bin/zed-setup`
   (checks the prerequisites and installs the git guard hooks).
3. Start herdr, open a pane in the Zed folder and run `claude`.
4. Say hi. On first run Zed **onboards you**: it asks who you are, how you work, which repos and
   tools you use, connects and verifies your accounts (GitHub, your ticket tracker, chat, …), and
   creates your profile on your own branch, `profile/<name>`. From then on it's your assistant.

## How it's organised

Everything outside `profile/` is **core**: Zed's capabilities, the same for everyone. Everything
about you lives in **`profile/`**, which exists only on your profile branch.

| Path | What |
|---|---|
| `AGENTS.md` | Zed's operating manual, one for both runtimes (OpenCode reads it; Claude Code reads `CLAUDE.md`, which imports it) |
| `playbook.md` | Lessons about tools and workflow that help every Zed |
| `.claude/skills/` | Zed's workflows: onboarding, new-task, check-workers, relay, wrap-up, learn |
| `templates/` | Core worker rules (system prompt) and the brief skeleton |
| `bin/zed` | Task DB and herdr session driver (`bin/zed help`) |
| `bin/zed-setup` | Prerequisite check + git guard hooks (`hooks/`) |
| `bin/zed-employee`, `bin/zed-deny` | Hats for workers (build, review, qa): `zed spawn hat=`, `zed-employee finish`, each hat's deny policy |
| `bin/zed-core-sync` | Carries core improvements from your profile branch to `main` |
| `profile.template/` | The skeleton onboarding copies to `profile/` |
| `profile/` | Yours: `profile.md`, `rules.md` (your rules for workers), `lessons.md`, `knowledge/`, `config/` (incl. `claude.json`: model, effort ceiling, max running sessions; `opencode.json`: model and variant of OpenCode workers), `briefs/`, `db/`, `STATUS.md` |

Only one Claude session per profile acts as Zed: it holds the profile's lock
(`bin/zed lock`). Other sessions opened in the checkout are read-only, or can take over.

`bin/zed` reads `profile/` by default; set `ZED_PROFILE_DIR` to point it at another
profile (handy for testing with a throwaway one).

## Configuration

Onboarding copies `profile.template/config/claude.json` to `profile/config/claude.json`: the `model`, the `effort`
ceiling, `effort_by_kind` (reasoning effort by kind of work, never above the ceiling) and `max_running` (open Claude
sessions, Zed's and yours included; `spawn` queues the rest). Without the file Claude Code's defaults apply.
`config/settings.json` holds `guard` (protected branches, commit format) and `worker.deny_tools`: the tool names workers
may not call. The template lists the write tools of one tracker and wiki connector so a new profile's workers cannot
post outward; replace them with your own connector's write tools.

`runtime` in `config/settings.json` (`claude` or `opencode`) names the one runtime the install uses; `bin/zed-setup`
writes it when exactly one of the two is installed, and without the key it is OpenCode only when Claude Code is not
installed. `profile.template/config/opencode.json` holds the `model` (`provider/model`) and `variant` Zed and its workers
run on OpenCode; without the file, or a key, the values in that template apply. `max_running` and the spend ledger do
not cover OpenCode workers.

On OpenCode (V2; tested on v2.2.7) Zed itself is started with `bin/zed coordinator`, not plain `opencode`. It writes the
session's config directory (model and variant, permission rules for Zed's routine commands, the profile's files as
instructions, a plugin that runs `bin/zed-pretool` before every tool call), starts a server for it, asks the model for
one call the guard always denies, and opens the TUI only when that call was stopped. `bin/zed start` refuses an OpenCode
session that was not opened this way. Not built yet on OpenCode: waking an idle Zed when a worker finishes (`start`
shows the watcher's events when it is run).

An OpenCode worker (V2; tested on v2.2.7) gets its own server, a background job of its herdr pane, whose config
directory holds Zed's rules (`AGENTS.md`) and the hat's deny plugin; spawn creates its session with the model and
variant, and before the brief asks the worker for one call that every hat denies. A worker whose guard does not stop
that call gets no brief and its session is marked `failed`. It reads the repository's `AGENTS.md` files and skills
(`.opencode/skills`, `.claude/skills`); it does not read `CLAUDE.md`. `tests/smoke_opencode.py` runs all of this against
the installed `opencode` (not part of the default test run): a worker's start, canary, denied calls, `AGENTS.md` and
skills; Zed's own canary, model and variant, `start`, the lock, and a denied edit and commit in a project repo.

Tests: `python3 -B -m unittest discover -s tests -v`.

## Branches

- `main` — core only. The guard hooks refuse commits on `main` that touch `profile/` and pushes
  of a `main` that contains it.
- `profile/<name>` — core + your profile. Zed runs here. Push it only to a remote you control and
  that may hold your work data (usually a private one).
- Improving Zed: when Zed learns something that helps any user, it commits that as a core-only
  commit and `bin/zed-core-sync` cherry-picks it onto `main`. To get others' improvements:
  update `main` (`git fetch && git branch -f main origin/main`, or pull in a separate worktree),
  then `git merge main` on your profile branch.

## Upgrading

`git merge main` on your profile branch is safe with an older profile: files and tables of removed features are
ignored, never read. `profile/employees/` (named employees, their memory and logs), the `employees`, `queue`, `inbox`,
`threads` and `messages` tables, the `employee`, `origin`, `verdict_sha`, `signed_by`, `assigned_hat`, `head_start` and
`head_end` session columns, `tasks.owner` (and others) and the `runtimes.json` config are unused now; delete them when
you like. Merging `main` never edits your profile files; if you edited core files on your branch, those conflict as in
any merge.
- **Runtimes.** If you cloned `main` while it supported Pi or Copilot: both are gone, and so is runtime selection
  (order, fallback, handoff, `runtimes.json`). Workers run on the install's runtime: on Claude Code with model and
  effort from `config/claude.json`, on OpenCode with model and variant from `config/opencode.json` (no effort).
  OpenCode has no blocking stop hook, so an OpenCode worker has no stop-guard: Zed's watcher flags a `done:` without `finish`.
- **Hats, not names.** Workers wear a hat (`spawn hat=build|review|qa`, default build); `spawn employee=` is refused.
  Whether a reviewer or QA authored the code is checked when the session finishes, not when it is assigned. A hat session
  is known by the herdr pane that hosts its process (found through the process tree and `herdr pane process-info`, not from
  `$HERDR_PANE_ID`, `$ZED_SESSION` or `$ZED_HAT`, which are only claims and are refused when they disagree), so it can
  finish only itself (`zed-employee finish`). Zed finishes a stuck worker with `--as-zed --session S<n>`, accepted only
  from a process in the pane of the session that holds the profile lock, with the lock's pid among its ancestors; a shell
  with no identity is never Zed. `zed-deny` also refuses a hat session `--as-zed` and setting or unsetting the identity
  variables (also through a name the shell expands, `env`, `export`, `unset`, wrappers, `bash -c`, `sudo`, `xargs`). Out of
  scope: a worker that edits the herdr or DB files directly, or runs its own script (it can also put its own `herdr`/`ps` first on `PATH`).
  OpenCode has no stop-guard; its workers run in herdr panes like any other, so the same identity applies.
- **Verdicts** given before the upgrade match no code (they now attach to the non-`profile/` tree), so redo review
  and QA for work still open.

## New machine

Clone, check out your profile branch from your remote, run `bin/zed-setup`, and start `claude` in herdr. Worker sessions and worktrees from the old machine won't exist; Zed sees them as `GONE`
and can respawn from their pushed branches/PRs. Re-store any Keychain tokens (see
`profile/profile.md` and `playbook.md`).
