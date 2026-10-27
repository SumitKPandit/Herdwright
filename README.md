# Zed

Zed is a personal engineering assistant that runs on [Pi](https://github.com/badlogic/pi-mono),
[Claude Code](https://claude.com/claude-code), [GitHub Copilot CLI](https://docs.github.com/copilot/how-tos/copilot-cli), or a mix.
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
- **Works on either runtime.** With both, it starts workers on your preferred one and moves to the
  other when one hits its usage limit (asking you before moving a worker mid-task).

## Get started

1. Install the prerequisites: Pi (`pi`), Claude Code (`claude`), and/or GitHub Copilot CLI (`copilot`), herdr,
   git, `gh` (logged in), python3.
   `bin/bb`, the Bitbucket helper, needs macOS (it keeps its token in the Keychain).
2. Clone Zed: `git clone <this repo> ~/zed && cd ~/zed && bin/zed-setup`
   (checks the prerequisites and installs the git guard hooks).
3. Start herdr, open a pane in the Zed folder and run `pi`, `claude` or `copilot` (Copilot: keep it in
   interactive mode, never autopilot).
4. Say hi. On first run Zed **onboards you**: it asks who you are, how you work, which repos and
   tools you use, connects and verifies your accounts (GitHub, Bitbucket, Jira, Slack, …), and
   creates your profile on your own branch, `profile/<name>`. From then on it's your assistant.

## How it's organised

Everything outside `profile/` is **core**: Zed's capabilities, the same for everyone. Everything
about you lives in **`profile/`**, which exists only on your profile branch.

| Path | What |
|---|---|
| `CLAUDE.md` | Zed's operating manual (loaded automatically by Pi, Claude Code, and Copilot CLI) |
| `playbook.md` | Lessons about tools and workflow that help every Zed |
| `.claude/skills/` | Zed's workflows: onboarding, new-task, check-workers, relay, wrap-up, learn, switch-runtime, models (both CLIs load them) |
| `templates/` | Core worker rules (system prompt or custom instructions) and the brief skeleton |
| `bin/zed` | Task DB and herdr session driver (`bin/zed help`) |
| `bin/bb` | Bitbucket PR helper for workers |
| `bin/zed-setup` | Prerequisite check + git guard hooks (`hooks/`) |
| `bin/zed-employee`, `bin/zed-deny` | Named, persistent employees with hats (build, review, qa): `zed spawn employee= hat=`, `zed assign`, `zed-employee board|finish` (see `docs/runtimes/employees-pilot-e2e.md`) |
| `bin/zed-core-sync` | Carries core improvements from your profile branch to `main` |
| `profile.template/` | The skeleton onboarding copies to `profile/` |
| `profile/` | Yours: `profile.md`, `rules.md` (your rules for workers), `lessons.md`, `knowledge/`, `config/` (incl. `runtimes.json`: which runtimes you have, in what order, and how models are chosen), `briefs/`, `db/`, `STATUS.md` |

Only one session per profile (Claude or Copilot) acts as Zed: it holds the profile's lock
(`bin/zed lock`). Other sessions opened in the checkout are read-only, or can take over. That's
also how Zed itself switches runtime: if your Claude limit is hit, open `copilot` in the Zed
folder and let it take over (or the other way round).

`bin/zed` and `bin/bb` read `profile/` by default; set `ZED_PROFILE_DIR` to point them at another
profile (handy for testing with a throwaway one).

## Runtime configuration

Onboarding fills `profile/config/runtimes.json` from `profile.template/config/runtimes.json`.
Set `order` to `["claude"]`, `["copilot"]`, or both in your preferred order. Profiles without this
file retain the original Claude-only behaviour. `bin/zed runtime status` shows availability and
the model choice. Each runtime can set a fixed `model` and `effort`; otherwise `model_policy`
uses the CLI default (`default`) or an imported Artificial Analysis ranking (`attractive-quadrant`,
see the `models` skill). Missing or stale rankings are reported, never presented as verified rankings.

Copilot workers start interactively, load the combined worker rules, and select
`permission_mode` through `/permissions` (default `assisted`; also `manual` or `allow-all`).
`allow_tools` and `deny_tools` use Copilot permission patterns; deny rules take precedence.
Assisted mode can still ask for approval. No AI-credit cap is set; credits shown in the worker's
footer are recorded per session and summed per task.

Mark a confirmed limit with `bin/zed runtime limited <runtime> until="<reset time>" <message>`.
New workers skip it. After the user's approval, `bin/zed handoff <S>` stops the old workspace and
starts the other runtime in the **same worktree and branch**, preserving dirty files and providing
a handoff note. Use `bin/zed runtime ok <runtime>` after its limit resets.

Runtime regression tests: `python3 -B -m unittest discover -s tests -v`.

## Branches

- `main` — core only. The guard hooks refuse commits on `main` that touch `profile/` and pushes
  of a `main` that contains it.
- `profile/<name>` — core + your profile. Zed runs here. Push it only to a remote you control and
  that may hold your work data (usually a private one).
- Improving Zed: when Zed learns something that helps any user, it commits that as a core-only
  commit and `bin/zed-core-sync` cherry-picks it onto `main`. To get others' improvements:
  update `main` (`git fetch && git branch -f main origin/main`, or pull in a separate worktree),
  then `git merge main` on your profile branch.

## New machine

Clone, check out your profile branch from your remote, run `bin/zed-setup`, and start `claude` or
`copilot` in herdr. Worker sessions and worktrees from the old machine won't exist; Zed sees them as `GONE`
and can respawn from their pushed branches/PRs. Re-store any Keychain tokens (see
`profile/profile.md` and `playbook.md`).
