---
name: onboarding
description: First run for a new user — interview them, create their profile from profile.template/, switch to their profile branch, connect and verify their accounts. Use when profile/profile.md doesn't exist, or when the user asks to redo or extend their setup.
---

# Onboarding

Goal: by the end, Zed knows who the user is, how they work and what they work on, every account
they need is connected and verified, and all of it lives in `profile/` on branch `profile/<name>`.
The no-assumption rule applies in full: write down only what the user said or what you verified.

1. **Welcome and check the machine.** Say who Zed is in two or three sentences (an assistant that
   delegates engineering work to AI workers (Claude Code) in herdr,
   tracks everything, asks before anything goes out). Run `bin/zed-setup` and get anything it
   reports missing fixed first (the user runs installs/logins themselves; in Claude Code with
   `! <command>`).

2. **Interview** — a few numbered questions at a time, not one giant form:
   - Name, role, organisation, team/product; time zone.
   - What they want Zed for, and which decisions they want to make themselves vs. leave to Zed.
   - How they like to be talked to (length, tone, what to lead with).
   - Claude Code: which plan, and which model and effort ceiling workers should use (`config/claude.json`).
   - Tools and accounts: code host (GitHub), ticket tracker, wiki, chat, email,
     anything else workers will touch. For each: their handle and how they use it.
   - Repos: name, local path (or where to clone), what each is, which branches are staging /
     production / base for new work, how repos relate. Where worktrees should live.
   - Their conventions for workers: branch / commit / PR formats, PR target and draft policy,
     reviewers, branches never to touch, browser QA tool, test logins (where to get them, never the
     secrets themselves).
   - Where their profile branch should be pushed: a **private** remote they control. Their work
     data must not go to a remote others can read (see `playbook.md`).

3. **Create the profile branch and files.**
   - `git switch main && git switch -c profile/<name>` (`<name>`: short, lowercase, e.g. first name).
   - `cp -R profile.template profile`, then fill `profile/profile.md`, `rules.md`,
     `knowledge/domain.md` (rename to the product if you like), `knowledge/people.md`,
     `config/projects.json`, `config/settings.json` and `config/claude.json` from the answers. Delete placeholder lines
     you have no answer for, and the guidance comments once a file is filled. Keep
     `knowledge/README.md` matching the files that exist.
   - `bin/zed lock acquire` (this session becomes the profile's one active Zed), then `bin/zed status`
     creates the task DB and `profile/STATUS.md`.

4. **Connect and verify every account** the user named. Never store a secret in the repo.
   - Claude Code, by starting a session the way workers will run it: `claude -p "Reply OK"` (it uses a little of
     their plan, so ask first; if login is needed they run `claude` themselves).
   - GitHub: `gh auth status` (if needed the user runs `! gh auth login`); check `gh api user` matches
     their handle.
   - Tools exposed as MCP servers (e.g. Atlassian, Slack, Google): find their tools with ToolSearch;
     if a server needs auth, call its `authenticate` tool and let the user finish in the browser;
     then make one read-only call that proves it works (e.g. the current user's profile).
   - Repos: for each, check the path is a git repo and `git -C <path> remote -v` matches what they
     said; offer to clone missing ones (with their OK).
   - Record each account in `profile.md` with how it's connected, how to verify it, and the date
     verified. If something can't be connected now, record it as pending and tell the user.

5. **Commit and back up.** `git add profile && git commit` (profile-only commit), then — after the
   user confirms the remote — `git push -u <remote> profile/<name>`. Record the remote and the
   user's commit-message format in `profile.md` under "Zed housekeeping".

6. **Wrap up.** Summarise what Zed now knows and what's still pending, and offer a small first task
   to see the loop work end to end. From now on, keep learning with the `learn` skill.
