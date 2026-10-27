---
name: review-maintainability
description: Review a diff for readability and long-term maintainability. Use for the judgement part of a review assignment.
---
# review-maintainability

Start from the facts `zed-review prep` printed (changed files without tests, debug leftovers, new dependencies,
migrations, large functions); do not redo them by hand. Then read the diff for:

- naming: will someone understand this in a year?
- cohesion and size: one change, one reason; functions that do too much
- duplication and fit with the repo's existing patterns
- test quality: do the tests fail when the code is wrong?
- error paths, and what breaks if an assumption is wrong

Every finding: `file:line`, severity (blocker / should-fix / nit), and the change that would fix it. End with one
verdict. Check the draft with `zed-review check-draft`. Never post it anywhere.
