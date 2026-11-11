# Knowledge index

<!-- Zed reads this index at session start and opens files when relevant. One row per file;
     update it whenever a file is added, split or renamed. -->

| File | What goes in it | Who reads it |
|---|---|---|
| `../profile.md` | Who the user is, accounts, preferences, Zed housekeeping | Zed |
| `../rules.md` | The user's rules for workers (appended to every worker's system prompt) | Zed + every worker |
| `../lessons.md` | How this user wants Zed to operate | Zed |
| `domain.md` | Products, apps, repos, branch roles, how repos relate | Zed (for briefs) |
| `people.md` | Team members, reviewers, account handles | Zed + workers raising PRs |
| `repos/<repo>.md` | Practical how-to per repo: setup, running, test logins, QA paths, gotchas | Workers on that repo |
| `../config/projects.json` | Machine-readable repo list: paths, base branches, worktree root | `bin/zed` |
| `../config/settings.json` | Tool settings (`guard`: protected branches, commit format; `worker.deny_tools`: tool names workers may not call; ships with one tracker and wiki connector's write tools, edit it to match your connectors) | `bin/zed-guard`, `bin/zed` |
