---
name: qa-web
description: Test a branch in a real browser like a user. Use for any change with a UI.
---
# qa-web

1. `zed-qa plan <task>` gives the case skeleton; add the odd paths you would try as a user.
2. Run the app per `knowledge/repos/<repo>.md` (how to run, test logins); use `playwright-cli`, or `agent-browser`.
3. Cover the happy path, each ticket edge case, and nearby regressions.
4. Evidence (screenshots, traces) goes to a directory outside the repo; name each file in the report.
5. Record the environment (URL, branch, commit) and any console errors. One verdict.
6. `zed-qa report-check <dir>` must pass before `zed-employee finish`.
