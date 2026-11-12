# Herdwright

Herdwright is a personal engineering assistant that runs on [Claude Code](https://claude.com/claude-code) or on [OpenCode](https://opencode.ai): an install uses one of them, for Herdwright and its workers alike.
You talk only to Herdwright. It takes your requests, writes a brief for each, and hands the work to
worker sessions running in [herdr](https://herdr.dev), one git worktree per session. It relays
their questions to you and your answers back, tracks every task, question and decision in a
small database, and asks you before anything goes out to other people.

What every Herdwright does, whoever uses it:
- **Delegates all the work and stays available.** Herdwright never codes itself; it's always free to talk.
- **Tracks everything.** Ask "what's in progress?" any time.
- **Asks before outward communication** — tickets, wiki, chat, email, PR comments.
- **Never assumes.** Herdwright and its workers ask instead of guessing.
- **QA before every PR**, in a real browser for anything with a UI.
- **Learns once.** Teach it something and it writes it down; you never repeat yourself.

## Get started

1. Install the prerequisites: Claude Code (`claude`), herdr, git, `gh` (logged in), python3.
2. Clone Herdwright: `git clone <this repo> ~/herdwright && cd ~/herdwright && bin/hw-setup`
   (checks the prerequisites and installs the git guard hooks; once you have a profile it also asks what to call
   your assistant, "Herdwright" unless you choose a name).
3. Start herdr, open a pane in the Herdwright folder and run `claude`.
4. Say hi. On first run Herdwright **onboards you**: it asks who you are, how you work, which repos and
   tools you use, connects and verifies your accounts (GitHub, your ticket tracker, chat, …), and
   creates your profile on your own branch, `profile/<name>`. From then on it's your assistant.

## How it's organised

Everything outside `profile/` is **core**: Herdwright's capabilities, the same for everyone. Everything
about you lives in **`profile/`**, which exists only on your profile branch.

| Path | What |
|---|---|
| `AGENTS.md` | Herdwright's operating manual, one for both runtimes (OpenCode reads it; Claude Code reads `CLAUDE.md`, which imports it) |
| `playbook.md` | Lessons about tools and workflow that help every Herdwright |
| `.claude/skills/` | Herdwright's workflows: onboarding, new-task, check-workers, relay, wrap-up, learn |
| `templates/` | Core worker rules (system prompt) and the brief skeleton |
| `bin/hw` | Task DB and herdr session driver (`bin/hw help`) |
| `bin/hw-setup` | Prerequisite check + git guard hooks (`hooks/`) |
| `bin/hw-employee`, `bin/hw-deny` | Hats for workers (build, review, qa): `hw spawn hat=`, `hw-employee finish`, each hat's deny policy |
| `templates/hats/`, `templates/skills/` | Each hat's charter, deny policy and skills. A worker's first prompt names its hat's skills on either runtime, and `hw-employee skill show <name>` prints one |
| `bin/hw-review` | The mechanical facts of a review and the check on its findings draft: `check-draft` refuses a review blocker that names no ground (`requirement`, `bug` or `rule <n>`) |
| `bin/hw-core-sync` | Carries core improvements from your profile branch to `main` |
| `profile.template/` | The skeleton onboarding copies to `profile/` |
| `profile/` | Yours: `profile.md`, `rules.md` (your rules for workers), `lessons.md`, `knowledge/`, `config/` (incl. `claude.json`: model, effort ceiling, max running sessions; `opencode.json`: model and variant of OpenCode workers), `briefs/`, `db/`, `STATUS.md` |

Only one Claude session per profile acts as Herdwright: it holds the profile's lock
(`bin/hw lock`). Other sessions opened in the checkout are read-only, or can take over.

`bin/hw` reads `profile/` by default; set `HW_PROFILE_DIR` to point it at another
profile (handy for testing with a throwaway one).

## Configuration

Onboarding copies `profile.template/config/claude.json` to `profile/config/claude.json`: the `model`, the `effort`
ceiling, `effort_by_kind` (reasoning effort by kind of work, never above the ceiling) and `max_running` (open Claude
sessions, Herdwright's and yours included; `spawn` queues the rest). Without the file Claude Code's defaults apply.
`config/settings.json` holds `guard` (protected branches, commit format) and `worker.deny_tools`: the tool names workers
may not call. The template lists the write tools of one tracker and wiki connector so a new profile's workers cannot
post outward; replace them with your own connector's write tools.

`runtime` in `config/settings.json` (`claude` or `opencode`) names the one runtime the install uses; `bin/hw-setup`
writes it when exactly one of the two is installed, and without the key it is OpenCode only when Claude Code is not
installed. `profile.template/config/opencode.json` holds the `model` (`provider/model`) and `variant` Herdwright and its workers
run on OpenCode; without the file, or a key, the values in that template apply. `max_running` and the spend ledger do
not cover OpenCode workers.

On OpenCode (V2; tested on v2.2.7) Herdwright itself is started with `bin/hw coordinator`, not plain `opencode`. It writes the
session's config directory (model and variant, permission rules for Herdwright's routine commands, the profile's files as
instructions, a plugin that runs `bin/hw-pretool` before every tool call), starts a server for it, asks the model for
one call the guard always denies, and opens the TUI only when that call was stopped. `bin/hw start` refuses an OpenCode
session that was not opened this way. OpenCode has no hook that runs when a turn ends, so there the watcher is one
long-lived process (`bin/hw watch --daemon`), started by `start` and, if it died, started again by `start` and by
`spawn`. For each event that needs Herdwright it prompts Herdwright's session through the session's server, which starts a turn of
an idle session, and keeps the id of the admitted message on the event as its receipt; when that call fails it types
the prompt into Herdwright's herdr pane instead. An event left un-acked is sent again after five minutes, three times at
most, and `start` always lists it. The watcher ends when Herdwright loses the profile lock or its server stops. Not covered:
a watcher that is killed while Herdwright is idle stays down until the next `start` or `spawn`; a wake that arrives while a
turn is running is admitted to the session's inbox, and how OpenCode then orders it was not tested.

An answer reaches a worker that is busy, on both runtimes: `bin/hw relay` prompts a worker that is idle and leaves a
working one alone. Its answer is recorded, the worker reads it between steps with `hw-employee answers`, and the
watcher prompts it with what it has not read once it is idle; an answer is given once. A worker that asks again, in the
same words in its status file, a question the user has answered gets the recorded answer again and the user is not
asked twice.

An OpenCode worker (V2; tested on v2.2.7) gets its own server, a background job of its herdr pane, whose config
directory holds Herdwright's rules (`AGENTS.md`) and the hat's deny plugin; spawn creates its session with the model and
variant, and before the brief asks the worker for one call that every hat denies. A worker whose guard does not stop
that call gets no brief and its session is marked `failed`. It reads the repository's `AGENTS.md` files and skills
(`.opencode/skills`, `.claude/skills`); it does not read `CLAUDE.md`.

What the guards are, on both runtimes: a check before each tool call and each plain shell command, which stops a worker
doing by mistake what its hat does not allow. They are not a sandbox. A worker that deliberately routes around them
through another program is not stopped: a script that writes files itself (`python3 -c …`), and on OpenCode the
server's terminal route (`/api/pty`, with the server password any process of the same user can read from the process
list) or a server the worker starts itself. On OpenCode the server password is kept out of the worker's shell, and the
server's two shell routes run the same check; Herdwright's own OpenCode session has the tool-call check only, because its
server's shell is the user's own (`!` in the TUI). A write is checked wherever its path points: absolute, relative to
the call's working directory, or after a `cd`; behind a compound command's opener or a wrapper (`{`, `then`, `do`,
`env`, `sudo`, `xargs`, `timeout`); as any redirect (`>`, `&>`, `>&`, after a heredoc), a file-writing program, `dd of=` or a
deleting `find`. A path the shell expands (`$DIR/x`) is not followed. Review and QA by sessions that did not write the code, and the git
guard, do not depend on these checks. `tests/smoke_opencode.py` runs all of this against
the installed `opencode` (not part of the default test run): a worker's start, canary, `AGENTS.md` and skills, and its
server's shell routes refusing what the hat denies, with no model asked for anything; Herdwright's own canary, `start`, the
lock, and its guard, called as its plugin calls it, denying an edit, a commit and a relative-path shell write in a
project repo, again with no model asked; and the wake: an idle session prompted through its server with the receipt
kept, the prompt typed into its pane when the server refuses, and one watcher only. Three checks need the model to do
what it is asked, so they can fail without a fault in Herdwright: that Herdwright's session runs the configured model and variant,
has the manual and the profile in its instructions and its skills, and runs `bin/hw start` without a permission
prompt (6 runs of 6 here on `github-copilot/claude-haiku-5.5` at `low`).

Tests: `python3 -B -m unittest discover -s tests -v`.

## Branches

- `main` — core only. The guard hooks refuse commits on `main` that touch `profile/` and pushes
  of a `main` that contains it.
- `profile/<name>` — core + your profile. Herdwright runs here. Push it only to a remote you control and
  that may hold your work data (usually a private one).
- Improving Herdwright: when Herdwright learns something that helps any user, it commits that as a core-only
  commit and `bin/hw-core-sync` cherry-picks it onto `main`. To get others' improvements:
  update `main` (`git fetch && git branch -f main origin/main`, or pull in a separate worktree),
  then `git merge main` on your profile branch.

## Upgrading

`git merge main` on your profile branch is safe with an older profile: files and tables of removed features are
ignored, never read. `profile/employees/` (named employees, their memory and logs), the `employees`, `queue`, `inbox`,
`threads` and `messages` tables, the `employee`, `origin`, `verdict_sha`, `signed_by`, `assigned_hat`, `head_start` and
`head_end` session columns, `tasks.owner` (and others) and the `runtimes.json` config are unused now; delete them when
you like. Merging `main` never edits your profile files; if you edited core files on your branch, those conflict as in
any merge.
- **The rename from Zed.** Herdwright was called Zed until November 2026: the command was `bin/zed` with helpers
  `bin/zed-*`, workers reported with `ZED:` markers, and settings were `ZED_*` environment variables. All of these are now
  `bin/hw`, `bin/hw-*`, `HW:` and `HW_*`. Close your worker sessions before you upgrade (a session started by the old
  code keeps the old names and cannot report to the new one). The first `bin/hw start` (on OpenCode, `bin/hw coordinator`)
  then renames what your profile still calls by the old name, once, and lists each rename: `db/zed.db` to `db/hw.db`,
  `.cache/zed.lock` to `.cache/hw.lock`, the `zed-deny` and `zed-pretool` plugin folders of OpenCode config folders
  under `.cache`, and the `refs/zed/core-sync/` git refs to `refs/hw/core-sync/`. The old `ZED_*` variables are not
  read: set `HW_PROFILE_DIR` where you set `ZED_PROFILE_DIR`; `start` names any that are still set. Git guard hooks
  installed in worktrees by the old code (`zed-hooks`) are not migrated: `bin/hw-guard audit` lists those worktrees.
- **Runtimes.** If you cloned `main` while it supported Pi or Copilot: both are gone, and so is runtime selection
  (order, fallback, handoff, `runtimes.json`). Workers run on the install's runtime: on Claude Code with model and
  effort from `config/claude.json`, on OpenCode with model and variant from `config/opencode.json` (no effort).
  OpenCode has no blocking stop hook, so an OpenCode worker has no stop-guard: Herdwright's watcher flags a `done:` without `finish`.
- **Hats, not names.** Workers wear a hat (`spawn hat=build|review|qa`, default build); `spawn employee=` is refused.
  Whether a reviewer or QA authored the code is checked when the session finishes, not when it is assigned. A hat session
  is known by the herdr pane that hosts its process (found through the process tree and `herdr pane process-info`, not from
  `$HERDR_PANE_ID`, `$HW_SESSION` or `$HW_HAT`, which are only claims and are refused when they disagree), so it can
  finish only itself (`hw-employee finish`). Herdwright finishes a stuck worker with `--as-hw --session S<n>`, accepted only
  from a process in the pane of the session that holds the profile lock, with the lock's pid among its ancestors; a shell
  with no identity is never Herdwright. `hw-deny` also refuses a hat session `--as-hw` and setting or unsetting the identity
  variables (also through a name the shell expands, `env`, `export`, `unset`, wrappers, `bash -c`, `sudo`, `xargs`). Out of
  scope: a worker that edits the herdr or DB files directly, or runs its own script (it can also put its own `herdr`/`ps` first on `PATH`).
  OpenCode has no stop-guard; its workers run in herdr panes like any other, so the same identity applies.
- **Verdicts** given before the upgrade match no code (they now attach to the non-`profile/` tree), so redo review
  and QA for work still open.

## New machine

Clone, check out your profile branch from your remote, run `bin/hw-setup`, and start `claude` in herdr. Worker sessions and worktrees from the old machine won't exist; Herdwright sees them as `GONE`
and can respawn from their pushed branches/PRs. Re-store any Keychain tokens (see
`profile/profile.md` and `playbook.md`).
