---
name: learn
description: Record something durable Herdwright has learned — a user correction, decision, preference, domain fact, or a better way to operate — in the right file (profile vs core), tidy, and committed. Use whenever the user teaches or corrects Herdwright, a worker reports Learnings, or Herdwright discovers a tool quirk.
---

# Learn

## 1. Decide where it goes

Ask: *would this be true and useful for a Herdwright serving someone else?*
- **No — it's about this user or their work** → the profile:

  | Fact | File |
  |---|---|
  | Who they are, accounts, preferences, Herdwright housekeeping | `profile/profile.md` |
  | A rule workers must follow (formats, tools, reviewers, never-touch branches) | `profile/rules.md` |
  | How this user wants Herdwright to operate (corrections, confirmed approaches) | `profile/lessons.md` |
  | Products, apps, repos, branch roles | `profile/knowledge/<domain>.md` |
  | People, handles, reviewers | `profile/knowledge/people.md` |
  | How to set up / run / log in / QA a repo; gotchas | `profile/knowledge/repos/<repo>.md` |
  | Repo paths, base branches, worktree root | `profile/config/projects.json` |
  | Model, effort ceiling, max running sessions | `profile/config/claude.json` |

- **Yes — it makes any Herdwright better** (a tool quirk, a workflow improvement, a missing step) → core:
  a lesson in `playbook.md`; a change to a skill, the manual (`AGENTS.md`), `templates/`, `bin/` or
  `profile.template/` when it changes how Herdwright works. Never put names, companies, repos, handles or
  other personal details in core — generalise the lesson.
- **Both** (e.g. "this tool takes reviewer IDs, not names" + this team's IDs) → split it: the general
  lesson to core, the specifics to the profile.

## 1b. Can it be code?
Before writing prose, ask: **can this be a script, hook, check or refusal?** (capability order: hooks, skills,
agents, MCP). If yes, build it (or, if it is more than a few minutes, record it as a task) and make
the lesson a one-line pointer to the code that enforces it, with the file and test. Prose-only is for judgment that
code cannot decide.

## 2. Write it well
1. **One home per fact.** Before writing, look for an existing entry; edit it in place instead of
   adding a contradicting line. Link from elsewhere rather than copying.
2. **Date volatile facts** ("as of 2026-10-26: releases/v4.44.1") so staleness is visible.
3. **Separate told from inferred.** Mark inferences `(inferred, unconfirmed)`; never brief a worker
   with an inference as if it were a decision.
4. **Lessons** carry the rule, **Why**, and **How to apply**.
5. **Mind the context budget.** `AGENTS.md`, `profile.md`, `lessons.md`, `playbook.md` and
   `failure-modes.md` are read at every session start, so together they have a word budget
   (`bin/hw start` shows it; the pre-commit hook fails when it is passed). When a
   lesson would push past it, prune or merge older entries, or move the detail into a skill or a
   `knowledge/` file that is loaded only when needed, leaving a one-line pointer behind.
6. **Prune.** If a file passes ~200 lines or mixes topics, split it and update
   `profile/knowledge/README.md`.

## 3. Commit
- Same turn. **Core and profile changes go in separate commits** (`git add profile/` for one,
  the core paths for the other), using the commit format in `profile.md`.
- After a core commit, run `bin/hw-core-sync` to see it queued for `main`, and `--apply` (plus
  pushing) as `profile.md` says. Tell the user in one line what was learned and where it went.
