---
name: qa-web
description: Test a branch in a real browser like a user. Use for any change with a UI.
---
# qa-web

1. `hw-qa plan <task>` gives the case skeleton; add the odd paths you would try as a user.
2. Run the app per `knowledge/repos/<repo>.md` (how to run, test logins); use `playwright-cli`, or `agent-browser`.
3. Cover the happy path, each ticket edge case, and nearby regressions.
4. Evidence (screenshots, traces) goes to a directory outside the repo; name each file in the report.
5. Record the environment (URL, branch, commit) and any console errors. One verdict.
6. `hw-qa report-check <dir>` must pass before `hw-employee finish`.

## Act like a user

- Test only through the browser: clicks, typing, key presses, scrolling, back and refresh.
- Find elements by their visible text, label or role, never by CSS or XPath selectors or test ids.
- Reach pages by clicking; do not type a deep URL the case does not give you.
- Never run JavaScript in the page, read its HTML or call the app's API to get past a hard-to-find control.
- Do not check a result with an HTTP request, a script or a database query. Judge it only by what is on screen: the
  page, its text and its accessibility snapshot.
- A case passes when the screen matches its expected result. It fails when the screen does not, or when a step cannot
  be done as written. It is blocked when its starting state cannot be reached. Retry a failure once from a fresh
  browser session before you record it.
- Take the expected result of a regression case from the brief or the ticket, never from what the code does. If
  neither says what the flow should do: `HW:QUESTION`.
- After the last case, spend about 10 minutes on the changed pages as a user would: back, refresh, cancel, invalid
  input, empty fields, a mobile-size viewport. Record what you find as observations; an observation alone does not
  fail a case.
- Test only the environment the brief names. Write no password in the report or the evidence.
