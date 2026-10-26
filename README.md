# Zed

Zed is a personal engineering assistant built on [Claude Code](https://claude.com/claude-code).
You talk only to Zed. It takes your requests, writes a brief for each, and hands the work to Claude
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
   `bin/bb`, the Bitbucket helper, needs macOS (it keeps its token in the Keychain).
2. Clone Zed: `git clone <this repo> ~/zed && cd ~/zed && bin/zed-setup`
   (checks the prerequisites and installs the git guard hooks).
3. Start herdr, open a pane in the Zed folder and run `claude`.
4. Say hi. On first run Zed **onboards you**: it asks who you are, how you work, which repos and
   tools you use, connects and verifies your accounts (GitHub, Bitbucket, Jira, Slack, …), and
   creates your profile on your own branch, `profile/<name>`. From then on it's your assistant.

## How it's organised

Everything outside `profile/` is **core**: Zed's capabilities, the same for everyone. Everything
about you lives in **`profile/`**, which exists only on your profile branch.

| Path | What |
|---|---|
| `CLAUDE.md` | Zed's operating manual (loaded automatically) |
| `playbook.md` | Lessons about tools and workflow that help every Zed |
| `.claude/skills/` | Zed's workflows: onboarding, new-task, check-workers, relay, wrap-up, learn |
| `templates/` | Core worker rules (every worker's system prompt) and the brief skeleton |
| `bin/zed` | Task DB and herdr session driver (`bin/zed help`) |
| `bin/bb` | Bitbucket PR helper for workers |
| `bin/zed-setup` | Prerequisite check + git guard hooks (`hooks/`) |
| `bin/zed-core-sync` | Carries core improvements from your profile branch to `main` |
| `profile.template/` | The skeleton onboarding copies to `profile/` |
| `profile/` | Yours: `profile.md`, `rules.md` (your rules for workers), `lessons.md`, `knowledge/`, `config/`, `briefs/`, `db/`, `STATUS.md` |

Only one Claude session per profile acts as Zed: it holds the profile's lock (`bin/zed lock`).
Other sessions opened in the checkout are read-only, or can take over.

`bin/zed` and `bin/bb` read `profile/` by default; set `ZED_PROFILE_DIR` to point them at another
profile (handy for testing with a throwaway one).

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

Clone, check out your profile branch from your remote, run `bin/zed-setup`, and start `claude`
in herdr. Worker sessions and worktrees from the old machine won't exist; Zed sees them as `GONE`
and can respawn from their pushed branches/PRs. Re-store any Keychain tokens (see
`profile/profile.md` and `playbook.md`).
