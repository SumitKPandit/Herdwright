# Design: a team of named, persistent employees (T18, revision 3)

Status: proposal for the user's review. Nothing here is built.
Revision 3 makes every employee a generalist: any of the six can do any kind of work (section 2). Revision 2
(`9ce5874`) is in git history.
Citations (`bin/zed:N`, `CLAUDE.md:N`, ...) are to core on the profile branch (`profile/sumit`), unchanged since
`2020c17` (checked: no diff in `bin/`, `templates/`, `CLAUDE.md`, `playbook.md`, `.claude/`). They are newer than the
`main` this branch was cut from (`main` lacks directive 9 and most `bin/zed-*` helpers). Citations of the form
`profile/config/...` are to the user's profile. Firstmate facts come from `docs/research/firstmate-learnings.md`
(branch `research/firstmate-learnings`) and a shallow read of github.com/kunchenguid/firstmate, read as data and
not run.

## 0. What changed from revision 2

| Revision 2 | Revision 3 | Why (the user's words) |
|---|---|---|
| Six fixed roles: senior SWE x2, SWE x2, QA tester, reviewer | Six **employees with no fixed role**. A role becomes a per-assignment **hat** (build, review, qa, design): a bundle of scripts, hooks and skills the assignment wears; any employee can wear any hat | "I'd like every employee to be able to do anything." |
| Assignee chosen by role, then by who is free | Chosen by **availability**: every free employee is bandwidth; among them, the one who knows the repo/task best (memory), seniors first for design and ambiguous work only | "If we have 6 employees, let all 6 employees be our work bandwidth." |
| Reviewer and QA tester were separate people by definition | Hard rule instead: **nobody reviews or QAs work they authored** | the same |
| Author idles or is sent back to fix | While their work is in review/QA an author takes other work (new, or review/QA of work finished by others) | "...or they can work on other stuff other authors have finished doing." |
| Zed wrote brief and plan | **Zed writes the brief (what/why); whoever takes the assignment writes its plan first (how, steps, estimates).** Larger work may start with a *design* assignment whose output is the plan | "Who writes the plan?" (answer accepted) |
| Memory `craft.md` per employee | Lessons are kept **per hat** (`craft/<hat>.md`), so a pack loads only the hat being worn | consequence |
| Pilot: Juno (SWE) + Quill (reviewer) | Pilot: Juno + Quill, each wearing build, review and qa in turn (two is the minimum that can satisfy the hard rule) | consequence |
| Build order 0-8 | Section 11 is rewritten as the buildable plan for steps 0-3 (work items, effort, order, done-when) | task brief |

Unchanged from revision 2: names and personalities, memory kinds and verification, messaging, runtime
neutrality, the capability order, no budgets, nobody retires.

## 1. What this is

The user's idea (verbatim in `bin/zed task show T18`): Zed as team lead of **named employees** with
persistent identity and memory; they differ from today's ephemeral workers; they may start ephemeral
subagents; Zed talks to them; they collaborate 1:1 or in groups; Zed keeps them productive and efficient.

Approved approach, unchanged: **an employee is a record on disk, not a process.** Identity and memory live in the
profile (git-versioned, human-readable). Each assignment starts a **fresh herdr session** loaded with only the
memory that matters for it; at the end the employee writes learnings back. No assignment, no session, no cost.

Why not a long-lived session per employee: it re-sends its whole context every turn (`bin/zed:813-846` already
restarts Claude workers past `context_refresh_tokens` for that reason), memory inside a context dies on a crash,
limit or restart, and it can't be inspected or verified. Files can.

**The org chart.** The user is the engineering manager. Zed is the team lead and the only one the user talks to by
default. The team is six employees, **all of whom can do all of the work** (section 2). There is no QA department
and no review department: any employee QAs or reviews what someone else built. Employees are *hired* on demand, so
the team grows by use.

### What "persistent" covers

| Persistent (on disk) | Ephemeral (per assignment) |
|---|---|
| name, personality, strengths, charter | the session, its context, its worktree use |
| memory: facts, preferences, lessons per hat, work, things heard | scratch notes, subagents |
| own skills, hook policy | the hat worn, the brief and the plan for that task |
| work log, mail | |

### Fit with the existing rules

- **Directive 9, capability order** (`CLAUDE.md:71-79`): hooks, skills, agents, MCP; every level leans on CLIs and
  scripts; deterministic over inference; clean context over bloated. This design applies it to *building the
  employees themselves*: each hat is assembled from scripts first, then hooks, then skills, and only what is
  left is "the agent" (section 3).
- **Directives 3-5** (`CLAUDE.md:53-62`): employees are workers. Product, team-lead and architecture decisions go
  to the user via Zed (`ZED:QUESTION`); a peer's answer never substitutes for one. Anything outward is a draft
  for Zed to show the user.
- **Directive 6** (QA before every PR): kept as is until an independent `qa` assignment has passed the pilot
  (decision 12 in section 12); then the `qa` hat replaces the author's own browser pass.
- **Directive 1**: Zed stays a dispatcher. Employees do the work in sessions, not in Zed's context.

## 2. The team

### Roster

Personality is the `voice` in `employee.md`; strengths are working habits. Neither is a permission and neither
limits which hat the employee may wear.

| Name | Personality | Strengths (habits, not roles) |
|---|---|---|
| **Atlas** (senior) | Steady and systems-minded; asks "what breaks if this is wrong?" and checks the cross-repo contract before writing anything. | Cross-repo contracts, data flow, failure paths, architecture trade-offs |
| **Forge** (senior) | Pragmatic; ships the smallest vertical slice that works, distrusts speculative abstraction, says plainly when a plan is too big. | Scoping, cutting a plan down, unblocking ambiguous work |
| **Juno** | Methodical and tidy; follows the codebase's patterns to the letter and keeps diffs small enough to review. | Pattern conformity, small diffs, test hygiene, migrations |
| **Pip** | Curious and quick; writes the test first, and says out loud what they don't know instead of guessing. | Debugging, reproducing bugs, test-first changes |
| **Scout** | Sceptical user-at-heart; tries the odd path first and reports exactly what they clicked and saw. | Exploratory testing, edge cases, exact repro notes, browser flows |
| **Quill** | Kind but exacting; asks "will someone understand this in a year?" and always names the change that would fix it. | Readability, naming, maintainability, spotting missing tests |

"Senior" has one meaning: **first pick for design and ambiguous assignments** (below). It confers no authority
over the others, no extra permissions and no say in product decisions.

### Hats

A hat is what an assignment wears: a bundle of scripts, hooks and skills (section 3). `spawn` takes `hat=`. The
employee's memory, personality and ordinary worker rules stay the same under every hat.

| Hat | Does | Writes to | Output |
|---|---|---|---|
| `build` | implements a plan on a branch; also used for **fixes** (a build assignment whose input includes review/QA findings) | the worktree (the only hat that may edit source) | commits, draft PR, `ZED:DONE` |
| `review` | reads the diff for correctness and long-term maintainability | nothing in the worktree | a findings draft with a verdict, for Zed to show the user (never posted) |
| `qa` | tests the branch like a user, in a real browser for UI work | evidence directory outside the repo | a QA report with evidence and a verdict |
| `design` | investigates an ambiguous or large task and produces **the plan**, split into assignable build units | the plan/design file only | a plan; no source edits |

### Assignment rule

1. **Who:** any employee with no open session is bandwidth. Among those, rank by deterministic familiarity (memory
   entries and past assignments on this repo, tags and task); seniors rank first for `design` and for work Zed
   marked ambiguous; ties go to whoever was assigned least recently. Zed confirms the pick (`zed-employee suggest`).
2. **Never your own work:** `spawn` refuses a `review` or `qa` assignment for anyone who wore `build` or `design` on
   that task/branch. This is checked in code from the DB, not asked of the model.
3. **Never idle while work waits:** an author whose work is in review or QA is free, and takes other open work
   (new, or review/QA of work finished by others); `zed-employee board` shows who is free and what waits.

### Who writes the plan

Zed writes the **brief**: what and why, the decisions the user already made, QA and done-when (`templates/brief.md`,
unchanged). **Whoever takes the assignment writes its plan first** (how, steps, rough times), as every worker already
does (`templates/worker-rules.md`, "Work to a plan"; `cmd_spawn` refuses a brief without a plan section,
`bin/zed:1283`). For large or unclear work Zed starts with a `design` assignment whose deliverable is the plan: a
list of build units, each with scope, "done when", estimate, dependencies and the files it touches, so units without
a dependency can go to different employees at once. Zed turns each unit into a brief (`zed-plan split`, section 3).

### How work moves (a typical story)

```
Zed writes the brief; `zed-employee suggest T20 --hat build` ranks the free employees; Zed assigns
  [large/ambiguous]  design  (a senior first)  -> plan with build units -> Zed assigns each unit
  build   anyone free                          -> branch + draft PR; the author is now free
  review  anyone free except the author(s)     ┐ may run together, read-only
  qa      anyone free except the author(s)     ┘ on the author's branch
  fixes   a build assignment: the author if free, otherwise anyone free; input = the findings (section 12, Q1)
  ready   `zed-pr ready-check` (existing)      -> Zed tells the user
```

Each stage is one assignment: a fresh session in the *same worktree/branch*, started only when no *writing*
session (`build`) is open on it; `review` and `qa` sessions may share a worktree with each other. `cmd_spawn`
already takes an existing `worktree` (`bin/zed:1259`, used by handoff at `bin/zed:1634`). In v1 Zed starts the
stages by hand using the `employees` skill and `zed-employee board`; chaining them by rule comes after the pilot.

### How much bandwidth six employees really are

Employees do not add capacity by themselves; they decide who fills the slots that exist. The existing limits still
apply unchanged: `max_running` per runtime counts every open session including Zed's and the user's own, and is
**4 for Claude and 3 for Copilot** in this profile (`profile/config/runtimes.json:13,26`; enforced by
`require_capacity`, `bin/zed:530`); all sessions on a runtime share one plan limit (`playbook.md:57-62`); and
`bin/zed-hostload` guards the Mac. Whether to raise `max_running` so that six can run at once is the user's call
(section 12, Q2). Until then the team is six interchangeable people sharing three or four desks.

## 3. Capabilities first, agents last

The user's order of priority: give each employee abilities through **scripts, CLI tools, programs and skills first,
and define a custom agent only for what those cannot do.** A hat is a *capability manifest*, and the "agent" is the
plain worker session Zed already spawns (`bin/zed:1259`), wearing that manifest.

```
templates/employees/hats/<hat>/
  hat.md              # charter: what this hat does, never does, escalates; 150 words
  capabilities.json   # the manifest (below)
  skills/<skill>/SKILL.md
templates/employees/roster/<name>.md   # the six personality/strengths cards, copied at hire
profile/employees/<name>/              # section 5
```

```json
{
  "scripts":  ["zed-plan", "zed-pr", "zed-checks", "zed-review"],   // CLIs the hat is told to use before thinking
  "skills":   ["review-maintainability"],                            // procedures loaded at their trigger
  "deny":     {"edit": ["**"], "commands": ["gh pr review", "gh pr comment", "bb pr comment"]},  // one policy, rendered per runtime (section 4)
  "subagents": "none",                                               // none | read-only | free
  "kind": "review"                                                   // feeds model_policy.effort_by_kind (`effort_for_kind`, `bin/zed:503`)
}
```

### Existing capabilities the team reuses (all deterministic, no model)

`bin/zed-plan` (plan progress), `bin/zed-pr` (PR state, blocking CI wait, ready-check), `bin/zed-guard` (the user's
git rules enforced in git itself), `bin/zed-crew` (what a session is really doing), `bin/zed-hostload` (is the
Mac healthy enough to start another session), `bin/bb`, and `playwright-cli` / `agent-browser` (the user's QA
tools, installed). The watcher already supplies STALL, USAGE, NO DELIVERY and context-refresh handling
(`bin/zed:669`, `bin/zed:2229-2241`, `bin/zed:2135-2162`, `bin/zed:813-846`).

### New scripts (each is useful to ordinary workers too, so they ship before any employee does)

| Script | Does (no model) | Used by hat |
|---|---|---|
| `zed-checks run <repo>` | runs the repo's lint, type check and tests from a `checks` block added to `config/projects.json` (today only in prose in `knowledge/repos/<repo>.md`), prints pass/fail and tails | build, review, qa |
| `zed-review prep <pr>` | diff stats; files changed without a test change; scan of added lines for debug leftovers (`console.log`, `print(`, `debugger`, `TODO`, commented-out code); new dependencies; migrations; large functions touched; secret pattern; CI state via `zed-pr`. Output is the facts section of a review | review |
| `zed-review check-draft <file>` | refuses a review draft where a finding lacks `file:line`, a severity or a suggested change, where there is no verdict, or where it contains anything addressed to a PR (it is a draft for Zed) | review |
| `zed-qa plan <task>` | extracts the ticket's acceptance criteria and the brief's definition of done into a numbered test-case skeleton (happy path, edge, regression) for the model to fill | qa, build |
| `zed-qa report-check <dir>` | refuses a QA report where a case has no result, an evidence file (screenshot/trace) named but missing, the environment (URL, branch, commit) unrecorded, console errors unrecorded, or no verdict | qa, build |
| `zed-xref <symbol|path>` | greps the symbol across every repo in `projects.json`, so contract changes show their other consumers | design, build |
| `zed-plan split <design.md>` | parses a design plan's build units into brief skeletons (one per unit, with its dependencies), ready for `bin/zed task add` | design, Zed |
| `zed-employee ...` | records, memory, pack, verify, finish, fix, report, suggest, board, skill loader (sections 5, 6, 8) | all |

### Per-hat capability stack

Order: scripts/CLIs, then hooks, then skills, then what is left for the model ("agent part"), then MCP. Skills
common to every hat: `answer-peer` (answer a peer's `ask` from memory and code first; escalate what is not theirs
to decide) and `finish` (write-back).

**`build`**
1. Scripts: `zed-plan`, `zed-checks`, `zed-guard`, `zed-pr`, `zed-xref` when a contract changes, the repo's own
   toolchain, `zed-employee`.
2. Hooks: git hooks from `zed-guard` (no commits to base, `JIRA-ID | ...` format); deny outward posting except the
   worker's own PR (the existing rule, `templates/worker-rules.md:105`).
3. Skills: `implement` (plan, code, `zed-checks`, self-review checklist); `raise-pr` (uses `zed-pr ready-check`);
   `address-findings` (turn a review/QA findings draft into plan steps, fix, re-run checks).
4. Agent part: writing and debugging code, nothing else. `kind` from the task; `subagents: none`.
5. MCP: none by default; what the task needs (e.g. Atlassian), as today.

**`review`**
1. Scripts: `zed-pr state`, `zed-review prep`, `zed-review check-draft`, `zed-checks`.
2. Hooks: deny all edits in the worktree, `gh pr review|comment`, `bb pr comment`, Jira and Confluence writes
   (read-only; pattern as `bin/zed-pretool:1-24`).
3. Skills: `review-maintainability` (readability and long-term maintainability: naming, cohesion, duplication,
   conformity with the repo's patterns, test quality; consumes `zed-review prep`'s facts); repo-specific skills the
   employee earned.
4. Agent part: the judgement on maintainability that no script has. Mechanical checks are scripts.
5. The hard rule applies: `spawn` refuses authors of the branch.

**`qa`**
1. Scripts/CLIs: `playwright-cli` (preferred) or `agent-browser`; `zed-qa plan`, `zed-qa report-check`;
   `zed-checks`; the repo's run and login facts from `knowledge/repos/<repo>.md` (and the employee's repo memory).
2. Hooks: deny edits to source files (QA reports, never fixes; the evidence directory is outside the repo); deny
   outward posting.
3. Skills: `qa-web` (happy path, ticket edge cases, nearby regressions, evidence directory convention);
   `bug-report` (repro steps template).
4. Agent part: exploratory judgement: which odd path to try, whether what is on screen is right.
5. MCP: none; the browser tool is a CLI. The hard rule applies.

**`design`**
1. Scripts: `zed-xref`, `zed-plan split`, `zed-checks` (to read the baseline), `zed-plan`.
2. Hooks: deny source edits (writes the design/plan file only); deny outward posting. `subagents: read-only`: the
   one hat where delegating exploration pays off in context.
3. Skills: `design-note` (a short decision record; anything with long-term impact, a new dependency or a
   cross-repo contract still goes to the user as `ZED:QUESTION`, per `templates/worker-rules.md:23-34`);
   `split-plan` (cut the plan into build units with scope, files, done-when, estimate, dependencies).
4. Agent part: design judgement and ambiguous investigation.

**Zed (team lead)**: `bin/zed`, `zed-employee suggest|board|report|postmortem`, the new `employees` skill (assign,
read mail, handle flags, propose fixes). No change to Zed's model policy.

### Agents: only where a measured need appears

No custom agent definition ships in v1. Every employee runs as a normal worker session (`runtime_launch`,
`bin/zed:1133`). A hat earns a dedicated agent definition (own tool allow-list, own model/effort) only if the
pilot shows a script, hook or skill cannot do what is needed, and the finding goes in `profile/employees/ideas.md`
(section 8). Subagents an employee starts itself are bounded by `capabilities.json` and share the plan's usage
limit (`playbook.md:57-62`); only Zed spawns employees.

## 4. Running on any runtime

### What firstmate does, and what we take

Firstmate runs the same fleet on more than a dozen harnesses. The pattern, from its repo:

1. **Truth is plain files, never the harness's own memory.** Its curated learnings, status, inboxes and per-task
   `meta` (with the exact `harness=`) are files; its contract says to keep them "regardless of harness memory"
   (`AGENTS.md` section 2; `state/<id>.status`, `state/<id>.inbox`). `CLAUDE.md` is one line, `@AGENTS.md`.
2. **One neutral definition, a thin adapter per harness.** A `harness-adapters` router skill plus
   `references/common/*` (dispatch, control, hooks, model/effort) and one `references/harness/<name>.md` of
   *dated, empirically verified* facts (busy signal, interrupt, exit, skill-invocation syntax, effort flag).
   Example of why: skill invocation is `/<skill>` on Claude and Cursor, `$<skill>` on Codex ("`/<skill>` is
   Claude-only and Codex rejects it"), and "no verified form" on OpenCode and Pi, where it falls back to plain
   language.
3. **Never dispatch on an unverified adapter; never generalise another harness's tool names without live
   evidence.** Where a hook cannot block, the doc states the compromise (`docs/turnend-guard.md`).
4. **Enforcement is in scripts**; hook files only adapt a harness's turn-end or pre-tool mechanism to a shared
   predicate (`bin/fm-turnend-guard.sh`).

### Rules for Zed's employee layer

- **R1. Files and the task DB are the only source of truth.** Employee records, memory, mail, plans, status and logs
  are plain markdown/JSON/SQLite in the profile. No `~/.claude` memory (`CLAUDE.md` directive 8), no runtime
  session memory, no path or tool name specific to one CLI inside a record.
- **R2. Policy is defined once, in the capability manifest, and rendered per runtime.** `deny` is the example:
  one list becomes a Claude `PreToolUse` hook, a Copilot `--deny-tool` list (`bin/zed:1151`), and, for anything that
  touches git, a git hook (`zed-guard`), which is runtime-neutral already.
- **R3. The enforcement point is a script; a hook is only a faster way to call it.** The write-back check is
  `zed-employee finish`, which the employee rules require and which the watcher re-checks on `ZED:DONE`. A Claude
  `Stop` hook may block earlier; on a runtime without one, the watcher check still catches it.
- **R4. Skills are portable files.** A skill is a `SKILL.md` in the hat or employee directory. It is loaded
  at its trigger with `zed-employee skill show <name>` (prints the file), which works on any runtime and keeps
  context clean; a runtime that has native skill discovery may also be pointed at the directory, as an
  optimisation, never a requirement.
- **R5. Mail is a table plus a doorbell.** Messages live in the DB. Delivery to a running employee is the
  employee's own cheap `bin/zed msg inbox` at the start of each plan step (a script call, any runtime), plus
  `herdr agent prompt` on an idle session (as the lapse nudge already does, `bin/zed:1912`), which also works
  for both runtimes. Hook delivery is an optional Claude extra.
- **R6. Per-runtime facts are written down, dated, and checked before reliance** (the adapter table below, kept
  in `docs/runtimes/<runtime>.md` after build step 0). An employee whose manifest needs a capability that is
  unverified on the chosen runtime falls back to the script-level enforcement of R3, or `spawn` queues it
  and tells the user (`switch-runtime`). Never a silent assumption.

### Adapter table (known from code now; the unknowns are verified in build step 0)

| Need | Claude | Copilot | Fallback if unavailable |
|---|---|---|---|
| Hat charter + rules | `--append-system-prompt-file` (`bin/zed:1139-1140`) | `*.instructions.md` in `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` (`bin/zed:1127-1129`, `1146`) | none needed; both known |
| Per-session identity and hat (`ZED_EMPLOYEE`, `ZED_HAT`) | herdr workspace `--env` (`bin/zed:1343-1345`; Claude currently passes none) | same mechanism; Copilot already passes one env | session id lookup in the DB |
| Deny outward/edit actions | `PreToolUse` hook (project-level today: `.claude/settings.json:20-52`; per-worker via `--settings`: **to verify**) | `--deny-tool` (`bin/zed:1151`) | `zed-guard` (git) + watcher + worker rule |
| Write-back/mail at turn end | `Stop` hook (works: `bin/zed:2272-2320`, project-level) | none known | `zed-employee finish` required by rules + watcher check on `ZED:DONE` |
| Skills | native `/<skill>` if the directory is added (**to verify**) | **to verify** | `zed-employee skill show` (R4) |
| Subagent cap | `PreToolUse` matcher `Agent` (**to verify**) | **to verify** | rule + `report` flag |
| Doorbell to an idle session | `herdr agent prompt` (`bin/zed:1186`, `1912`) | same | none needed |
| Prompt to a *working* session queues, not interrupts | **to verify** | **to verify** | deliver at step boundaries (R5) |
| Limits / credits | `limit_patterns`, `claude_usage` (`bin/zed:62-72`) | `limit_patterns`, `credits_pattern` (`bin/zed:73-84`) | existing `switch-runtime` |

## 5. The employee record

Core ships hat templates and the six roster cards; the profile holds instances (they contain the user's work data,
so they follow the profile's private-remote rule, `playbook.md:16-19`).

```
profile/employees/<name>/
  employee.md                          # identity, voice, strengths, limits (below)
  memory/
    craft/<hat>.md                     # how this employee does that hat well: lessons, no repo facts
    craft/general.md                   # lessons that hold under every hat
    repos/<repo>.md                    # facts, preferences, work and things heard, per repo / system
    archive.md                         # dropped entries, with reason (git has the rest)
  skills/<skill>/SKILL.md              # skills this employee earned (hat defaults come from the template)
  log.md                               # generated: one line per assignment, with its hat (never hand-edited)
profile/employees/ideas.md             # generated/appended: "a script could do this" proposals (section 8)
```

`employee.md` (frontmatter is what scripts read; no runtime-specific field except an optional preference). There is
**no `role` field**: what the employee does is decided per assignment.

```markdown
---
name: quill
voice: "Kind but exacting. Asks whether someone will understand this in a year, and always names the fix."
strengths: [readability, naming, maintainability, missing tests]   # shown on the intro card; not read by `suggest`
senior: false                 # true only for first pick on design/ambiguous work
hired: 2026-10-28
runtime: auto                 # auto = the profile's runtime order (runtimes.json); a name only if the user wants one
memory_words: 1500            # budget of the memory pack handed to each session
---
# Quill
(personality and working habits copied from the roster card at hire; the user's edits live here)
```

Everything an assignment needs beyond this (kind, `deny`, skills, subagent mode) comes from the hat template, so a
hat fix reaches every employee who wears it. `status` is not a field: nobody retires (section 12).

### Memory (decision 4: they may remember anything they worked on or are aware of)

Memory is markdown with one strict header line per entry, so scripts can parse, verify, sort and cap it without a
model. Five kinds:

| kind | holds | needs | goes stale |
|---|---|---|---|
| `fact` | how a repo or system works | `src` (file path + `anchor` string, or URL) | when the anchor is gone, or after `ttl` (default 60 d) |
| `preference` | what the user or team wants | `src` naming a decision (`decision T7 E12`, PR URL) | after `ttl` (default 180 d) |
| `lesson` | how to wear a hat better, or work better generally; stored in `craft/<hat>.md` or `craft/general.md` | `src` (the assignment that taught it) | never; pruned by low `uses` |
| `work` | what the employee did: task, **hat**, branch, PR, outcome, what was tricky | written by `zed-employee finish` **from the DB and plan, no model**; the employee may add one line | `ttl` 180 d, then archived |
| `heard` | something learned from a peer or from Zed that the employee did not verify | `src: msg #<id>` | `ttl` 30 d unless confirmed (`refresh` upgrades it to a `fact` with an anchor) |

Example, `profile/employees/quill/memory/repos/unified-mention.md` (a `work` entry names its hat, so `suggest` can count familiarity per repo and per hat):

```markdown
- [m-0007] fact | 2026-10-20 | tags: auth,views | src: app/auth/guard.py anchor: "def require_role" | verified: 2026-10-27 | ttl: 60d | uses: 4
  Role checks live in `guard.py`, not in views. A view that skips `require_role` is a finding.
- [m-0011] preference | 2026-10-22 | tags: prs | src: decision T17 E41 | verified: 2026-10-22 | ttl: 180d | uses: 2
  The user wants approval only when there are no blockers, otherwise a short list of blockers.
- [m-0014] work | 2026-10-26 | tags: auth | hat: review | src: T17 S31 PR#512 | ttl: 180d | uses: 0
  Reviewed PR#512 (role guard refactor): two findings, both about error paths; author fixed in one round.
```

Rules enforced by `bin/zed-employee remember` (not by prompt): date is stamped by the script; `src` is mandatory;
a code `anchor` must exist in the cited file on the repo's base branch *now*, so every entry starts verified;
entries over 120 words, matching a secret pattern (credentials are never remembered, whatever the kind), or
duplicating an existing one (normalised text) are refused; a new entry citing the same `src` as an existing one
with different text is accepted but flagged `conflicts: m-0003` for the employee to merge.
"Remember anything" is about *what may be stored*; the limits are about *what is true and what loads into
context*.

Pack selection is deterministic, no model: the `lesson`s of the hat being worn plus `craft/general.md`, by `uses` (at most 400 words); `fact`/`preference`/
`heard`/`work` whose repo matches and whose tags intersect the assignment's (or all of the repo's when no tags
are given), newest `verified` first, until `memory_words` is reached (`work` capped at 150 words). Entries past
`ttl` are included only as `UNVERIFIED: re-check before relying`, at most 5. `stale` entries (anchor gone) are
*not* included; they are listed under "Needs your attention", and the employee must `refresh` or `drop --reason`
each in its first steps. The pack records the ids it used (`uses` +1) so unused entries can be pruned.

### Stale or wrong memory (the main risk)

1. Every entry is dated and source-linked, or it is refused.
2. `verify` is deterministic: for each `fact` with an anchor it runs `git show origin/<base>:<path>` and searches
   for the anchor; missing = `stale`, past `ttl` = `expired`. It runs before every pack for that repo and, for all
   repos, in `bin/zed start` when the last run is older than 7 days.
3. Unresolved stale entries block nothing but are re-shown at the top of every pack; two assignments unresolved =
   auto-archived to `archive.md` (recoverable; also in git).
4. Facts not checkable in git (Confluence page ids, ticket state) are `ttl`-only, marked `check: manual`.
5. Memory is written only through `remember`/`refresh`/`drop`. A deny rule (R2) refuses direct edits to
   `profile/employees/**` from an employee session.
6. Memory never overrides the code, the ticket or the user: the employee rules say "memory is a lead, not a
   source; cite what you actually read".

## 6. Lifecycle: choose, assign, work, write back, fix

### Choosing: `zed-employee board` and `suggest` (deterministic, no model)

`bin/zed-employee board` prints, from the DB and the employee files: **who is free** (no running or waiting
session), and **what waits**: queued tasks, branches whose `build` finished and lack a `review` or `qa` verdict,
and fix requests (a verdict of `blockers` with no later `build`). Each waiting item shows who may take it. Zed reads
the board at `bin/zed start` and after every `ZED:DONE`; that keeps all six busy without Zed holding the picture.

`bin/zed-employee suggest <task> --hat <hat>` ranks the free employees:

1. **Eligible:** free; and for `review`/`qa`, not an *author* of the target. Authors are the employees recorded in
   `sessions` with hat `build` or `design` on that task or branch.
2. **Lead first:** for `design`, or work Zed marked ambiguous at intake, `senior: true` employees rank first.
3. **Familiarity:** a score from the employee's memory files and log: count of non-stale entries for the task's repo
   (more for matching `tags`), plus earlier assignments on the same task (a fixer who built it ranks high), plus
   earlier assignments with the same hat on the repo.
4. **Spread:** ties go to the least recently assigned, then to name order, so the result is reproducible.

It prints the ranking with the numbers behind it; Zed confirms or overrides. Familiarity is a preference among free
people, never a reason to wait for one who is busy. A flag (section 8) fires if one employee takes more than half
of a repo's assignments over 30 days, so familiarity cannot quietly turn into specialisation.

### The assignment

```
Zed: bin/zed spawn T20 ... employee=quill hat=review tags=auth,views origin=zed   (alias: bin/zed assign quill T20 ...)
  1. checks: employee exists; no other open session for them (else queued); capacity (`require_capacity`,
     bin/zed:530); hat is known; hard rule (review/qa refused for an author of the task/branch);
     one writer: a `build` is refused while any other session is open on the worktree, and any session is
     refused while a `build` is open on it
  2. bin/zed-employee verify --repo <repo>      # cheap local git; marks entries ok / stale / expired
  3. bin/zed-employee pack quill --repo <repo> --hat review --tags auth,views --session S42
       -> profile/.cache/employees/quill/S42-pack.md   (budget: memory_words ≈ 2-3k tokens)
  4. spawn as today (bin/zed:1259), plus: agent name quill-t20; sessions.employee='quill', sessions.hat='review';
     ZED_EMPLOYEE and ZED_HAT env; hat charter and employee rules appended to the worker rules; the hat's `deny`
     rendered for the chosen runtime (R2); first prompt names the charter, voice, pack, inbox, the findings of
     earlier assignments on this task (the DB `findings` messages) and the skills available (loaded with
     `zed-employee skill show`)
  5. the fresh session writes its plan first (the plan file the brief names), then works to it (plan file, status
     file; STALL/NO DELIVERY/USAGE/context refresh apply unchanged)
  6. before ZED:DONE:  bin/zed-employee finish [--verdict clean|blockers] --lesson "..." (zero or more)
       OR  finish --none "why nothing new"
       (finish: writes the `work` entry from the DB; records the verdict for `review`/`qa`; runs the deterministic
        checks; appends log.md; and asks "did you do anything by hand more than once that a script could do?" ->
        ideas.md)
  7. ZED:DONE -> Zed's normal wrap-up (CLAUDE.md "Lifecycle" 6); wrap-up also commits profile/employees/<name>/
```

A fresh session per assignment follows what Zed already does for context hygiene: state is carried by files
(plan, status, handoff notes), not by a long context (`bin/zed:835-846`, `bin/zed:1573`). `spawn` gains an
`origin=` field (`zed` | `user` | later `schedule`) recorded on the session; it changes nothing now (decision 9,
section 12).

### Review and QA findings, and the fix loop

`review` and `qa` end with a draft and a verdict. The draft goes to Zed (never to a PR); `clean` lets the branch move
toward `zed-pr ready-check`, `blockers` creates a fix request on the board. The **fix** is a `build` assignment whose
brief is the findings draft and whose first plan step is to turn each finding into a step (`address-findings`). It
goes to the author if free, otherwise to anyone free; either way the session starts from the plan, status file and
findings, not from the author's memory of the work (section 12, Q1). After the fix, the same check is re-run by
someone who is not an author, **including the previous fixer**: the author set grows with every `build`.

### Fixing an employee (nobody retires)

An employee that goes wrong is repaired, not removed. The loop, all deterministic until the last step:

1. **Signal**: a flag (section 8) or the user's complaint. 2. `zed-employee postmortem S42` prints the evidence
bundle: the pack ids that session used and their text, the hat, the entries cited in any send-back, the plan
timeline, the messages sent, the `finish` output. 3. Zed (the model) decides the repair from that bundle, which is
one of: `drop`/amend a memory entry with a reason; add a `lesson` from the failure (to the hat's `craft` file); tighten
a skill, hook or charter in the hat template or the employee's directory; add a script for what the employee kept
doing by inference. 4. The repair is a file change committed in the profile with its reason
(`ZED | Fix quill: drop m-0007 (stale)`), and the next pack carries it. A hat-wide repair (charter, skill, hook,
`capabilities.json`) is shown to the user first; Zed alone may drop or amend a single memory entry (decision 12, Q3).

## 7. Messaging

Employees (and Zed) talk through messages stored in the task DB so Zed can see everything. Zed is an implicit
member of every thread. Employees do not message the user: the user "talks only to Zed" by default
(`CLAUDE.md:4`; decision 6). If the user wants an employee directly, see "Direct chat" below.

### DB (added to `SCHEMA`, `bin/zed:92-144`; migration pattern as `bin/zed:159-166`)

```sql
CREATE TABLE IF NOT EXISTS employees (
  name TEXT PRIMARY KEY, senior INTEGER NOT NULL DEFAULT 0, hired_at TEXT NOT NULL, introduced_at TEXT);   -- index; files are the truth
ALTER TABLE sessions ADD COLUMN employee TEXT;                -- an assignment is a session
ALTER TABLE sessions ADD COLUMN hat TEXT;                     -- build | review | qa | design
ALTER TABLE sessions ADD COLUMN verdict TEXT;                 -- review/qa only: clean | blockers (set by `finish`)
ALTER TABLE sessions ADD COLUMN origin TEXT;                  -- zed | user | (later) schedule
ALTER TABLE sessions ADD COLUMN direct INTEGER NOT NULL DEFAULT 0;   -- the user is talking to this session
CREATE TABLE IF NOT EXISTS threads (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL,                  -- dm | group
  subject TEXT NOT NULL, task_id INTEGER REFERENCES tasks(id),
  status TEXT NOT NULL DEFAULT 'open',                         -- open | closed | needs_zed
  max_messages INTEGER NOT NULL DEFAULT 16, created_by TEXT NOT NULL,
  created_at TEXT NOT NULL, closed_at TEXT);
CREATE TABLE IF NOT EXISTS thread_members (thread_id INTEGER REFERENCES threads(id), member TEXT NOT NULL,
  PRIMARY KEY (thread_id, member));                            -- employee names, or 'zed'
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL REFERENCES threads(id),
  sender TEXT NOT NULL, ts TEXT NOT NULL,
  kind TEXT NOT NULL,                                          -- ask | answer | handoff | findings | fyi
  needs TEXT,                                                  -- an ask must say what answer it needs
  reply_to INTEGER REFERENCES messages(id), body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS receipts (message_id INTEGER REFERENCES messages(id), member TEXT NOT NULL,
  delivered_at TEXT, PRIMARY KEY (message_id, member));
```

Memory write-backs reuse the `events` table (`kind='memory'`, `bin/zed:136`); spend reuses `db/spend.jsonl` with an
`employee` field added to the row (`bin/zed:868-881`).

### Commands (`bin/zed msg ...`)

```
msg open dm|group "<subject>" <member...> [--task T]     # group: 2-4 members; subject is mandatory
msg send <thread|member> --kind ask --needs "<answer>" "<text>"   # to a member = find-or-open the DM
msg inbox [<name>]                  # unread for me (employees default to themselves)
msg thread <id>                     # full thread
msg list [--unread|--open|--flagged]   # Zed's view of everything
msg close <thread> ["<resolution>"]
```

`msg` is the one `bin/zed` command employees may run. It does not need the coordinator lock (`bin/zed:257`), it
identifies the sender from `ZED_EMPLOYEE` / the session id, and it can only touch the message tables. The worker
rules' "do not change the coordinator's task database" (`templates/worker-rules.md:7-8`) gets this one exception.

### Free, but efficient (decision 5)

Employees may start a thread, open a group, ask any peer, with **no approval step**. Efficiency, not permission,
is what is enforced:

- Every message has a `kind`; an `ask` must say what answer it needs (`needs:`); `findings`/`handoff` carry the
  deliverable (e.g. a `review` assignment's findings draft) so whoever fixes starts from it, not from a summary.
- No message may contain a secret pattern (same scan as memory).
- A thread passes `max_messages` (default 16): it flips to `needs_zed` and a watcher event shows Zed the thread, so
  Zed can extend, redirect or close it. This is the single hard stop; it exists to catch ping-pong.
- An `ask` unanswered for 30 min while its target has a running session, and any `needs_zed` thread, become watcher
  events.
- Efficiency flags in `report` (section 8): messages per delivered unit, unanswered asks, threads closed without a
  resolution.

A message is information or a request between peers. It never carries a product, team-lead or architecture
decision: those go through `ZED:QUESTION` to the user via Zed, and the employee rules say so. Any employee may answer
a peer's question in the "decide yourself" class (local implementation, existing patterns,
`templates/worker-rules.md:23-34`); never a question in the "ask" class.

### Delivery (R5)

- **To a running employee:** the employee runs `bin/zed msg inbox` at the start of each plan step (rules + a
  one-line script call, any runtime). The watcher also rings an *idle* session with `herdr agent prompt`. A Claude
  `Stop` hook that blocks once on unread mail is an optional extra.
- **To an employee with no session:** it waits. "No employee runs without an assignment", so mail never starts
  one. `bin/zed start` lists mail waiting for idle employees, and, when the mail is `findings` for an open task,
  proposes the assignment. When the employee is next assigned, unread mail is part of the pack.

### Direct chat (decision 6)

By default the user hears from employees through Zed ("Quill (review) found...": name plus the hat worn). When the user wants to talk
to one: `bin/zed talk <name> [T]` finds the employee's open session, or starts one with a normal pack and
`sessions.direct=1`, and tells the user which herdr pane to attach to (the pane id is stored with every session).
For a direct session Zed's watcher keeps tracking and reporting, but never prompts or closes it (the rule for
external sessions, `CLAUDE.md` Lifecycle), and the employee's questions go to the user in that pane. The employee
still runs `finish` at the end, so what was learned in the chat is remembered.
## 8. How Zed oversees productivity and efficiency (no budget)

There is no token or credit budget per employee. The goal is that every employee, and Zed, **reach for the cheapest
adequate tool first** (script, then hook, then skill, then the model) and keep context clean. That is built in,
and measured, not capped.

Already enforced for every worker, and so for employees, unchanged: plan file at spawn (`bin/zed:1283`), `STALL`
after 30 min with no plan progress (`bin/zed:669`, `bin/zed:2205-2220`), `USAGE` without progress every 0.5M output
tokens (`bin/zed:2229-2241`), `NO DELIVERY` probes (`bin/zed:2135-2162`), context refresh (`bin/zed:813-846`),
`max_running` and ceilings (`bin/zed:482-540`), the Stop-hook guard for the watcher (`bin/zed:2272`).

By construction (the capability order made concrete):

| Mechanism | Effect |
|---|---|
| Hat manifest lists the scripts to use first, in the charter | the employee is told what to run before thinking |
| Skills load only at their trigger (`zed-employee skill show`) | nothing sits in the context "just in case" |
| Memory pack is capped, hat- and tag-selected, session is fresh per assignment | clean context instead of a growing one |
| `finish` asks "did you repeat anything by hand?" and appends the answer to `profile/employees/ideas.md` | repeatable inference becomes a script request; Zed turns it into code (directive 9) |
| Pack, `verify`, `finish` checks and `report` use zero model tokens | design rule |

New, all script-side:

1. **Write-back check.** On a `ZED:DONE` for a session with `employee`, the watcher requires a `finish` record for that
   session; otherwise it emits `DONE without write-back` and Zed sends it back. This is the runtime-neutral check
   (R3); a Claude `Stop` hook may have blocked it first.
2. **Mail check.** Watcher events for `needs_zed` threads and stale asks.
3. **`bin/zed-employee report [name] [--days 30]`**, computed from the DB, spend ledger and memory files:
   assignments **by hat**, minutes, output tokens per plan step done (the metric `spend_line` already prints,
   `bin/zed:884-894`), deliverables landed, send-backs (`events kind=sendback`), messages sent/answered, memory
   entries written/dropped/stale, pack size, entries never used in 5 packs, `ideas.md` entries open.
4. **Flags** (each a one-line watcher or `start` finding, with its evidence): tokens-per-step above 2x the employee's
   own median; two send-backs in a row; memory growing while `uses` stay 0; more than 6 messages per delivered unit; **an employee free for more than a day while the board has work they may take** (utilisation: the point of six generalists is that none sits idle); **one employee holding more than half of a repo's assignments over 30 days** (familiarity hardening into specialisation); a `review`/`qa` verdict of `clean` followed by a send-back at `ready-check` (a hat that misses things);
   an entry cited in a send-back (probable wrong memory: starts the fix loop, section 6); the same idea proposed twice
   in `ideas.md` (a script is overdue).
5. **Digest.** `bin/zed start` gets one line per employee (last assignment, flags, unread mail, open ideas). Zed
   reads the digest, not the logs, and wakes the user only for a flag that needs a decision.

Model judgement remains for: confirming who takes a new assignment (`zed-employee suggest` ranks the free employees; Zed confirms), reading a flagged pack or thread, and choosing a repair.

**Not measured, a hypothesis for the pilot:** that the pack (about 2-3k tokens read once, plus 0-5 memory
writes) costs less than the re-derivation it saves. The pilot (section 11) compares against T17's recorded
sessions in `db/spend.jsonl`.

## 9. Onboarding and introductions

Today onboarding (`.claude/skills/onboarding/SKILL.md`) interviews, creates the profile, connects accounts. Changes:

1. New interview question after "What they want Zed for": *Do you want the team? Which of the catalogue (Atlas,
   Forge, Juno, Pip, Scout, Quill) do you want hired now?* Zed shows each card (personality, strengths, the four hats
   they can wear). The user may rename or re-voice a card, or add an employee from a blank card. Nobody is hired into a
   role, because there are none; at least two must be hired for the review and QA rule to be satisfiable.
2. After the profile exists and accounts are verified, `bin/zed-employee hire <name>` per employee:
   copies the roster card into `profile/employees/<name>/`, writes `employee.md`, registers the DB row. Strengths
   may be extended from the user's repos/domain (`profile/knowledge/`), confirmed by the user.
3. **Introduction**: `bin/zed-employee intro <name>` prints a short card from the files (name, personality, strengths, the hats
   they wear, what they will never do without approval, how they remember). Zed presents the cards in chat and
   records `introduced_at`. No worker session starts for this.
4. Existing users: `hire` any time; the onboarding skill's "extend setup" path covers it.
5. `profile.template/` gains `employees/README.md` and a `config/settings.json` block
   `{"employees": {"verify_days": 7, "thread_max_messages": 16}}`; `config/projects.json` gains a per-repo `checks`
   block (`lint`, `types`, `test`) used by `zed-checks`.

## 10. What changes in the repo

| Area | Change | Effort |
|---|---|---|
| `bin/zed-checks`, `zed-review`, `zed-qa`, `zed-xref` (new, one file each); `bin/zed-plan` (gains `split`) | scripts in section 3, with tests in `tests/test_<script>.py`; useful to every worker, no employee needed | M-L |
| `bin/zed-employee` (new; helpers in `bin/lib/zedlib.py`) | `hire`, `list`, `show`, `intro`, `remember`, `refresh`, `drop`, `verify`, `pack`, `finish`, `skill show`, `suggest`, `board`, `postmortem`, `report` | L |
| `bin/zed` | `SCHEMA` + `sessions.employee/hat/verdict/origin/direct` migration; `spawn employee= hat= tags= origin=` (hard rule, one-writer check, pack, name, rules, env, deny rendering); `assign` alias; `talk`; `msg` command group; watcher events (write-back, mail); `start` digest and board lines; `record_spend` field; optional `hook employee-*` | L |
| `docs/runtimes/<runtime>.md` | the adapter facts of section 4, dated | S |
| `templates/` | `employees/hats/<hat>/` (hat.md, capabilities.json, skills) for build, review, qa, then design; `employees/roster/<name>.md` for the six; `employee-rules.md` appended after worker rules | M |
| `.claude/skills/` | new `employees`; edit `onboarding`, `new-task` (decide hat, mark ambiguous work, choose design-first), `check-workers` (mail, flags, send-back, board), `wrap-up` (commit employee dir) | S |
| `profile.template/` | `employees/README.md`; settings blocks of section 9 | S |
| `CLAUDE.md` | at most 120 words: Tools line, Lifecycle step 2 mention, pointer to the `employees` skill (always-loaded context is 7,250 of 9,000 words today, `bin/zed:2322-2347`) | S |
| `failure-modes.md` | rows: wrong/stale memory; employee chatter; DONE without write-back; employee drifting from script-first; an employee idle while work waits | S |
| `tests/` | memory format/verify/pack, hard rule, one-writer rule, suggest ranking, spawn with employee and hat, msg limits, deny rendering per runtime, the new scripts | M |

Core and profile changes go in separate commits (`CLAUDE.md` Housekeeping).

## 11. Build plan and pilot

Effort: S about hours, M about a day, L about 2-3 days of one worker. These are estimates, not measurements.
Each step is usable on its own. Capabilities come first because they help today's workers immediately and every
later step depends on them.

| # | Step | Effort | Done when |
|---|---|---|---|
| 0 | **Adapter probe** | S-M | each "to verify" cell of section 4 has a dated yes/no/fallback |
| 1 | **Capability scripts** | M-L | an ordinary worker can use each script; tests green |
| 2 | **Record + memory** | L | memory format validated; pack deterministic in tests |
| 3 | **Assignment with hats** | M-L | a Juno build, a Quill review and a Juno-or-Quill qa run end to end on both runtimes (or queue with the stated fallback) |
| 4 | **Pilot, phase A** (about a week, at least 4 assignments per employee across all three hats, no messaging) | M | measured against the T17 baseline |
| 5 | **Messaging and direct chat**: tables, `msg`, limits, doorbell, step-boundary inbox, `talk`, `start` mail line | L | Phase B exchange works; no thread hits `needs_zed` |
| 6 | **Oversight**: `report`, flags, digest line, `postmortem`, `ideas.md`, failure-modes rows | M | flags fire on seeded bad data in tests |
| 7 | **Rest of the team and the `design` hat**: Scout, Atlas, Forge, Pip hired; `design` template and `zed-plan split` skill; onboarding skill, `CLAUDE.md`, `profile.template/` | M | each employee has passed one real assignment under two hats; one design assignment produced a plan Zed split into briefs |
| 8 | **Flow automation** (stage chain build → review/qa → ready by rule; Zed auto-assigns from the board) | M | only if the pilot shows the manual pattern is stable |

Steps 0-3 are the work to do now; they are detailed below. 4 is the pilot's solo phase; 5-6 gate its collaborative
phase.

### Steps 0-3 as work items

Each item is one worker assignment (or less), with its own plan, tests and PR. Order: 0, 1 and 2 are independent and
may run in parallel (three workers fit under `max_running` 4 with Zed; they share one plan limit, `playbook.md:57-62`);
3 needs 0 and 2. Critical path: 2 then 3, about 5-6 working days; total about 10-12 worker-days. The ids are for
`bin/zed task add`.

**Step 0: adapter probe** (one item, S-M)

| Id | Work | Effort | Done when |
|---|---|---|---|
| 0.1 | Probe on the pinned Claude Code and Copilot, in a scratch worktree, with real sessions: per-worker hook settings (`--settings`), `ZED_EMPLOYEE`/`ZED_HAT` through herdr `--env`, `Agent` matcher for the subagent cap, native skill discovery from a directory, a prompt sent to a *working* session (queues or interrupts), `Stop` hook on Copilot. Write `docs/runtimes/claude.md` and `copilot.md` | S-M | each cell has a dated yes/no and the fallback of R3-R5 where no |

**Step 1: capability scripts** (items 1.1-1.5 independent; 1.6 last)

| Id | Work | Effort | Done when |
|---|---|---|---|
| 1.1 | `checks` block in `config/projects.json` (+ `profile.template/`), `bin/zed-checks run <repo>` | S-M | runs lint/types/tests of a real repo; pass/fail and tails; test with a fake repo |
| 1.2 | `bin/zed-review prep <pr>` and `check-draft <file>` | M | `prep` output on a real PR lists each fact of section 3; `check-draft` refuses a draft missing `file:line`, severity, suggestion or verdict |
| 1.3 | `bin/zed-qa plan <task>` and `report-check <dir>` | M | skeleton from a real brief; `report-check` refuses each failure listed in section 3 |
| 1.4 | `bin/zed-xref <symbol\|path>` | S | finds a known symbol in two repos of `projects.json` |
| 1.5 | `bin/zed-plan split <design.md>` (extends the existing script) | S | a sample design plan yields one brief skeleton per unit, dependencies kept |
| 1.6 | Mention all new scripts in `templates/worker-rules.md`; word count check on always-loaded context | S | ordinary workers are told they exist; context budget still under 9,000 words |

**Step 2: record and memory** (2.1 first; 2.2-2.5 follow, then 2.6)

| Id | Work | Effort | Done when |
|---|---|---|---|
| 2.1 | `SCHEMA`: `employees` table and `sessions.employee/hat/verdict/origin/direct` columns with the migration pattern (`bin/zed:159-166`); no behaviour change | S | existing tests pass on a migrated DB |
| 2.2 | `templates/employees/`: hats `build`, `review`, `qa` (charter, manifest, skills), the six roster cards, `employee-rules.md` | M | manifests validate against a schema check in tests; each skill loads with `skill show` |
| 2.3 | `zedlib` memory format: parse/write entries, `remember`, `refresh`, `drop` with the refusals of section 5 (secret, duplicate, missing `src`, missing anchor, over 120 words) | M | one test per refusal; stamped date; conflicts flagged |
| 2.4 | `verify` (anchors against `origin/<base>`) and `pack` (hat-aware, tag-selected, budgeted, ids recorded) | M | same inputs give a byte-identical pack; stale entries excluded and listed |
| 2.5 | `bin/zed-employee hire`, `list`, `show`, `intro`, `skill show` | S-M | hire Juno and Quill in a throwaway profile; intro cards print from files |
| 2.6 | Tests for 2.1-2.5 in `tests/test_zed_employee.py` | M (spread across items) | green |

**Step 3: assignment with hats**

| Id | Work | Effort | Done when |
|---|---|---|---|
| 3.1 | `spawn employee= hat= tags= origin=`: capacity, one-writer rule, hard rule (authors from `sessions`), pack call, agent name, `ZED_EMPLOYEE`/`ZED_HAT`, `assign` alias | M | tests: review refused for an author, `build` refused while a reader is open, accepted otherwise |
| 3.2 | Render the hat's `deny` per runtime (Claude hook, Copilot `--deny-tool`, git hook), using the 0.1 findings; deny direct edits to `profile/employees/**` | S-M | a denied command is blocked in a real session on each runtime, or the fallback is recorded |
| 3.3 | `finish` (work entry from DB, verdict, checks, `log.md`, `ideas.md` question) | M | refuses a missing verdict for `review`/`qa`; deterministic output |
| 3.4 | Watcher: `DONE without write-back` event; `start` lists the board | S | seeded session without `finish` raises the event |
| 3.5 | `zed-employee suggest` and `board` (section 6) | M | table-driven tests: hard rule, senior first for design, familiarity, spread tie-break, reproducible order |
| 3.6 | End-to-end on both runtimes with Juno and Quill: Juno `build`; Juno `review` of that branch refused (hard rule); Quill `review` and `qa`; a fix by Juno; re-review by Quill | M | transcript of each assignment, `log.md` lines, board output |

(3.6 also shows the rule's edge: with only two employees a fix must go to the author, or nobody is left to
re-review. That is why Q1 matters for the pilot.)

### Pilot: two employees

Recommended: **Juno and Quill**, as in revision 2 (the user accepted the pair), now both wearing build, review and qa.
Two is the minimum that can satisfy the hard rule at all: Juno builds and Quill reviews and QAs; the next task
swaps. Scout joins third; with the hat model Scout is not "the tester" but another employee, so the change to rule 6
is decision 12 in section 12 (Q2 there), not a new question.

- **Build (either):** takes ordinary stories and bugs on a repo with good tests. Success is checkable from data:
  send-backs per task fewer than comparable T-sessions in `spend.jsonl`; tokens per plan step at or below baseline;
  memory `uses` over 40% of packed entries after 5 assignments.
- **Review (either, on the other's PRs and, separately, unapproved PRs reusing T17's work):** drafts only; never
  posts (deny rule). Success: findings per review that the author accepted; `zed-review check-draft` passes first
  time; tokens per plan step at or below T17's.
- **QA (either, on the other's branches):** `zed-qa report-check` passes first time; defects found that the author's
  own pass missed (count, compared with tasks QA'd by the author alone).
- **Phase A (solo, about a week):** no messaging. Measure the above against T17 and recent worker baselines in
  `db/spend.jsonl`.
- **Phase B (collaborative):** turn on `msg`. Planned exchanges: the `review` assignment asks the author what a change was meant
  to do (`ask`, needs a sentence); `findings` go to the thread and Zed starts the fix assignment from them (no Zed
  summarising turn). Success: at most 4 messages per assignment, every `ask` answered, no thread reaches `needs_zed`.
- **Exit review with the user:** `bin/zed-employee report` for both, flags raised, utilisation (was either idle
  while work waited?), `ideas.md` entries and what was scripted from them, and what the user felt (fewer questions?
  better reviews?). Then hire the rest of the team.

## 12. Decisions (the user's answers) and what is still open

### Decisions (recorded verbatim; "Applied" says where they landed)

1. **Roles and capabilities** (rev 1 answer). *"I or the user will be the engineering manager, you'll be the team lead,
   you'll need 1 or 2 senior software engineers, a couple of software engineers, a QA team member which actually tests
   everything on the app like user using playwright-cli or agent-browser and a reviewer who will check code quality for
   long term maintainability and readability. While I'm saying all these, you shouldn't map these directly to agents
   because that comes 3rd in priority order. You try to give each employee these capabilities through scripts, cli tools,
   programs, skills etc before trying to define them directly as agents."*
   Applied: sections 1-3. **Superseded in part by decision 11**: the capabilities of the senior, SWE, QA and reviewer
   roles survive as hats any employee can wear; the user is the engineering manager and Zed the team lead (confirmed,
   decision 5 of the rev-2 questions).
2. **Names.** *"You can name them in whatever you like. Give them some personality."* Applied: section 2.
3. **Runtimes.** *"Yeah! We should build in way that you and your employees should be able to run on all kinds of
   harness or runtime like firstmate."* Applied: section 4 (R1-R6, adapter table, build step 0).
4. **Memory.** *"It's okay for employee to remember anything they have worked on or are aware of."* Applied: section 5.
5. **Peer messaging.** *"Let them freely communicate as long as they are being efficient and productive."* Applied:
   section 7.
6. **Talking to employees.** *"I would prefer they communicate to me through you unless I want to talk to someone
   directly."* Applied: section 7 ("Direct chat").
7. **Personality and attribution.** *"Sure."* (reports attributed by name, with a light personality rather than
   role-play). Applied: section 2.
8. **Budgets.** *"No budget. Just want each employee and you to be efficient by following the prioritization order when
   reaching out for tools, deterministic over non-deterministic (LLM Calls) and efficient context over bloated."*
   Applied: section 8.
9. **Standing assignments.** *"Not for now. Hopefully, later if we want to add proactive nature then it wouldn't be too
   hard."* Applied: `origin=` on every assignment (section 6).
10. **Retirement.** *"Nobody retires, we fix them if they go wrong somewhere."* Applied: section 6.
11. **Every employee can do anything (revision 3).** *"Honestly, I would want each employee to be able to do
    everything. We don't want some to sit idle and only some to do heavy duty work. If we have 6 employees, let all 6
    employees be our work bandwidth."* and, asked to confirm: *"Yes. I'd like every employee to be able to do
    anything."* Applied: roles become per-assignment hats (section 2, 3); assignment by availability and familiarity
    (sections 2, 6); never review or QA your own work; employees take other finished-by-others work while theirs is
    in review.
12. **Answers to the revision-2 questions.** *"1. Okay. Who writes the plan? 2. Sure. 3. Sure. 4. Yes. Or they can
    work on other stuff other authors have finished doing. 5. Yes."* Applied:
    - Q1 pilot pair Juno + Quill: **kept**, both now wearing build, review and qa (section 11).
    - "Who writes the plan?": Zed writes the brief; whoever takes the assignment writes its plan first; large work may
      start with a design assignment (section 2). Zed's answer, accepted.
    - Q2 browser QA: *recommended and accepted*: until a `qa` assignment has passed the pilot, authors keep doing the
      real-browser QA that `CLAUDE.md` directive 6 asks for; after that, a `qa` assignment by a non-author does it and
      the PR needs a `zed-qa report-check`-clean report (sections 1, 11). With hats the "tester" is whoever is free.
    - Q3 fixes to employees: *accepted*: Zed alone may drop or amend a single memory entry (with a reason,
      committed); a change to a hat's charter, skill, hook or `capabilities.json` is shown to the user first
      (section 6).
    - Q4 serial review then fix on one worktree, plus *"or they can work on other stuff other authors have finished
      doing"*: accepted; the one-writer rule (section 2) and the "never idle" assignment rule.
    - Q5 org chart: user is the engineering manager, Zed the team lead (section 1).

Capability order (hooks, skills, agents, MCP; deterministic over LLM calls; clean context over bloated) is
`CLAUDE.md` directive 9 and is the organising principle of sections 3 and 8.

### New open questions (product or team-lead decisions)

1. **Who does the fixes?** After a `review` or `qa` verdict of `blockers`, the fix is a `build` assignment.
   Recommended: the author if free at that moment, otherwise anyone free (the session starts from plan, status file
   and findings). Alternative: always the author, even if that leaves a free employee idle and waits for the author's
   own work to end. Note: with only two employees (the pilot) the fix must go to the author, because a second fixer
   would leave nobody eligible to re-review (every fixer joins the author set). Which?
2. **Is "all six are bandwidth" up to six at once?** Today `max_running` is 4 for Claude and 3 for Copilot, counting
   Zed's and your own sessions (`profile/config/runtimes.json:13,26`), and every session on a runtime shares one plan
   limit (`playbook.md:57-62`). Recommended: keep the caps; the six employees fill the slots by availability, and
   you raise `max_running` only if you accept a faster burn of the plan limit. Alternative: raise it now so six can
   run at once. Which?
3. **Does the author set include `design`?** The hard rule bars anyone who wore `build` from reviewing or QAing that
   work. Recommended: also bar whoever wore `design` on the same task, because a designer judging a build of their
   own plan is reviewing their own work in the same sense. Alternative: allow it, which leaves more eligible people,
   which matters most while only two are hired. Which?
