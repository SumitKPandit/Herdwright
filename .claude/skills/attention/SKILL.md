---
name: attention
description: Build the user's "what needs my attention" report across GitHub, Bitbucket, Jira and Confluence. Use when the user asks what is on their plate, or for the daily attention push.
---

# Attention report

`bin/zed-attention` reads GitHub, Bitbucket and Jira itself (read-only; settings in `profile/config/settings.json` →
`attention`). Confluence cannot be read by a script: the `zed-confluence` token reads pages but gets 401 on comments and
search. So Confluence is a feed that I collect with the Atlassian connector, then hand to the script.

1. **Collect Confluence (read-only connector calls only).** With `searchConfluence` (CQL) look for, since the report's
   `--since`: pages that mention the user (`mention = currentUser() AND lastmodified >= "<YYYY-MM-DD>"`) and new comments by
   others on pages the user created (`creator = currentUser() AND type = page`, then the comments of those pages via
   `getConfluenceContent`). Write rows to a scratch file, one per comment or mention:
   `{"url", "title", "who", "ask", "updated" (ISO), "created"?, "resolved"?}`. Never create, edit or comment.
2. **Run** `bin/zed-attention --since <ISO|YYYY-MM-DD> --confluence-json <file>` (`--json` for machine reading).
   Exit 1 means a source failed: it is listed under `!` with the missing scopes when known; report it, don't hide it.
3. **Tell the user** what needs them, in the script's order: review requests, open PRs with no approval, own PRs with new
   review activity, Jira, Confluence. Link every item.

Without step 1 the script prints Confluence as a note ("not read"). Nothing here posts, approves or comments.
