# Zed

Zed is a Claude Code persona that manages engineering work for the Mention EM: it takes requests,
delegates them to Claude worker sessions running in [herdr](https://herdr.dev) (one git worktree
per session), relays their questions, and keeps a record of everything.

## Layout
- `CLAUDE.md` — Zed's operating manual (loaded automatically).
- `bin/zed` — task tracker (SQLite at `db/zed.db`) and herdr session driver. `bin/zed help`.
- `STATUS.md` — generated snapshot of all work.
- `config/projects.json` — repos, paths, base branches.
- `knowledge/` — durable facts about Mention and the user.
- `templates/` — worker rules (appended to every worker's system prompt) and brief skeleton.
- `briefs/` — per-session briefs.
- `.claude/skills/` — Zed's workflows: new-task, check-workers, relay, wrap-up.

## Setup on a new machine
1. Install: Claude Code (`claude`), herdr, git, gh (logged in), python3.
2. Clone the Mention repos to the paths in `config/projects.json` (or edit the paths).
3. Clone this repo, start herdr, open a pane in this folder and run `claude`.
   Say hi to Zed. Sessions/worktrees from the old machine won't exist; Zed will see them as
   `GONE` and can respawn from their pushed branches/PRs.
