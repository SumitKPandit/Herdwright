---
name: new-task
description: Intake a new piece of work from the user, record it, write a brief and spawn worker session(s) in herdr. Use whenever the user asks for something to be built, fixed, investigated or changed.
---

# New task

1. **Collect facts — never assume.** Check the profile first (`profile/knowledge/`, `profile/lessons.md`,
   `profile/config/projects.json`), then ask for what's still missing. Need, per task:
   - Ticket ID, if the user works with tickets (`profile.md` says which tracker). If none exists, ask
     whether to work without one or to create one (creating = outward, needs approval of the exact text).
   - Anything the user's branch format needs (e.g. a worktype taken from the ticket type — see
     `profile/rules.md`); ask if it doesn't map obviously.
   - Repo(s) involved and, per repo, the base branch. Defaults from `profile/config/projects.json` and
     `profile/knowledge/` are *suggestions*: state them and get a yes. Follow any base-branch rules in
     `profile/lessons.md` (e.g. release branches that change — check recent PRs and confirm).
   - Goal and acceptance criteria (read the ticket yourself to avoid asking what's already written;
     ask only about real gaps).
   - Anything cross-repo.
   Ask all missing items in one numbered message.
2. `bin/zed task add title="..." jira=<ticket-id> worktype=<worktype> description="..."` → T-id
   (`jira=` holds any ticket ID). Record each confirmed decision with
   `bin/zed log <T> "decision: ..." kind=decision`.
3. Per repo, `bin/zed brief <T> repo=<repo> base=<base> branch=<branch per profile/rules.md>`: it fills
   everything mechanical (context, QA environment from the repo's knowledge file, plan block, done
   criteria) into `profile/briefs/T<id>-<repo>.md` and lists the `<FILL: …>` spots. Write only those:
   goal, decisions (verbatim), open points, acceptance criteria, from confirmed facts only. List open
   points explicitly so the worker asks rather than guesses. `spawn` refuses a brief with any
   `<FILL …>` left.
4. If the model policy needs a ranking and `bin/zed models show` says it is missing or stale,
   refresh it (`models` skill) before spawning. If the source cannot be read, tell the user and
   get their model choice; never claim a CLI default is benchmark-optimal.
   `bin/zed spawn <T> repo=<repo> base=<base> branch=<branch per profile/rules.md> brief=profile/briefs/T<id>-<repo>.md`
   (branch description: short kebab-case; confirm with the user if it isn't obvious). It picks the
   runtime (first usable one in `config/runtimes.json`) and the model/effort (model policy); its
   output says which and why. If it says no runtime is available, tell the user (`switch-runtime`).
5. Make sure the watcher is running (see CLAUDE.md "Runtimes"). Tell the user: T-id, session(s),
   worktree, branch, runtime and model. Commit the profile.
