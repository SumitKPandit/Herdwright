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
   - For UI-specific backlog planning, have a worker inspect the relevant Figma/design source
     and distinguish its approval state before finalizing component, variant, state and visual
     acceptance scope. Generic component families are not design evidence. Surface missing
     links/access first; do not ask for details the source can answer. This prevents invented
     scope without blocking independent app bootstrap, tooling or test foundations.
   - Anything cross-repo.
   Batch genuine missing items in one numbered message, or use focused rounds when the user requests a dialogue.
   **For iterative discussions:** maintain one current baseline and a separate dated history of verbatim answers.
   After every answer, reconcile all affected requirements, dependencies, alternatives and active questions in place.
   Retire or replace questions whose premises were resolved or superseded, including their DB entries; keep the history,
   not contradictory current sections. Before asking again, check the full confirmed baseline. Separate decisions the
   user must make from technical facts to verify; never ask the user to guess those facts.
   **PRD versus implementation readiness:** draft the PRD from confirmed user needs, scope and acceptance outcomes;
   unresolved technical verification or sizing does not block that draft. List genuine gaps explicitly.
   Technical discovery can inform feasibility, but neither an old specification nor inspected code substitutes for
   Product approval. Finalize the technical specification against reviewed product requirements, without treating
   the PRD as proof of integration readiness or a delivery commitment.
2. `bin/zed task add title="..." ticket=<ticket-id> worktype=<worktype> description="..."` → T-id.
   Record each confirmed decision with
   `bin/zed log <T> "decision: ..." kind=decision`.
3. Per repo, `bin/zed brief <T> repo=<repo> base=<base> branch=<branch per profile/rules.md>`: it fills
   everything mechanical (context, QA environment from the repo's knowledge file, plan block, done
   criteria) into `profile/briefs/T<id>-<repo>.md` and lists the `<FILL: …>` spots. Write only those:
   goal, decisions (verbatim), open points, acceptance criteria, from confirmed facts only. List open
   points explicitly so the worker asks rather than guesses. `spawn` refuses a brief with any
   `<FILL …>` left.
4. `bin/zed spawn <T> repo=<repo> base=<base> branch=<branch per profile/rules.md> brief=profile/briefs/T<id>-<repo>.md`
   (branch description: short kebab-case; confirm with the user if it isn't obvious). It picks the
   model from the runtime's config file; its output says which and why.
5. Make sure the watcher is running (see the manual, "Runtime differences"). Tell the user: T-id, session(s),
   worktree, branch and model. Commit the profile.
