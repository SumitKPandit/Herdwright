---
name: new-task
description: Intake a new piece of work from the user, record it, write a brief and spawn worker session(s) in herdr. Use whenever the user asks for something to be built, fixed, investigated or changed.
---

# New task

1. **Collect facts — never assume.** Need, per task:
   - Jira ID (MENT-…). If none exists, ask whether to work without one or to create one (creating = outward, needs approval of the exact text).
   - Worktype for the branch prefix (ask if unclear; known examples: `story`).
   - Repo(s) involved and, per repo, the base branch. Defaults from `config/projects.json` are
     *suggestions*: state them and get a yes. For `cm-fe` / `cm-api` the release branch changes —
     check recent PRs (`gh pr list -R AirPR/cm-fe --state all -L 10`; cm-api is on Bitbucket)
     and confirm with the user.
   - Goal and acceptance criteria (read the Jira ticket yourself to avoid asking what's already written; ask only about real gaps).
   - Anything cross-repo (e.g. cm-reports medusa → onclusive-core-client dist).
   Ask all missing items in one numbered message.
2. `bin/zed task add title="..." jira=MENT-123 worktype=story description="..."` → T-id.
   Record each confirmed decision with `bin/zed log <T> "decision: ..." kind=decision`.
3. Per repo, copy `templates/brief.md` to `briefs/T<id>-<repo>.md` and fill it with confirmed facts
   only. List open points explicitly so the worker asks rather than guesses.
4. `bin/zed spawn <T> repo=<repo> base=<base> branch=<worktype>/<jira-lower>-<desc> brief=briefs/T<id>-<repo>.md`
   (branch desc: short kebab-case; confirm with the user if it isn't obvious).
5. Make sure the watcher is running (see CLAUDE.md "Watch"). Tell the user: T-id, session(s),
   worktree, branch. Commit the Zed repo.
