---
name: code-rules
description: The 21 checkable rules for changed code. Use in a build's self-review and as the only rule set of a review.
---
# code-rules

Check every changed line against each rule, on the change's diff: `git diff <base branch>...HEAD` plus, in a build,
the uncommitted changes and each untracked file (`git status --porcelain`). The base branch is in the brief. "The plan"
is your session's plan file; "the brief" includes the answers Zed relayed. A build fixes the line that fails a rule. A
review only reports it, as a finding with the rule's number, and changes nothing. A fix that needs a choice neither
the brief nor an existing convention settles: `ZED:QUESTION`.

| # | Rule | Check |
|---|---|---|
| 1 | Every changed hunk maps to a step of the plan. | Name the step for each hunk. Revert any hunk without one. |
| 2 | Follow the nearest existing convention for naming, error type, logging, imports and file layout: the same file first, then the same module, then the repo. | Name the existing code you followed. Two conventions that conflict: `ZED:QUESTION`. |
| 3 | Each function does one thing. | Describe it in one sentence without "and". |
| 4 | Names use the domain words of the brief. Booleans read as questions, such as `isActive` or `hasAccess`. Use only abbreviations the repo already uses. | Compare each new name with the brief's wording. |
| 5 | A literal with meaning gets a named constant, placed with similar constants. | No unexplained number or string in the diff. |
| 6 | Validate input at the entry point where it enters the system: handler, CLI, message consumer. | Validation appears only at entry points. |
| 7 | Errors use the repo's existing error type, and the message names the operation, the identifier and the reason. Never swallow an error: no empty `catch`, and no `catch` that returns `null` or a default to hide the failure. Catch only what you handle. | Read every `catch`, `except` and `rescue` in the diff. |
| 8 | Change every place where the same rule lives, or reuse one shared copy. | Search the repo for the value or condition you changed. Each place found is in the diff or calls the shared copy. |
| 9 | No code for future needs: no unused parameter, option, flag or abstraction. | Every new symbol has a caller in the diff. |
| 10 | Nothing left behind: no commented-out code, debug output or TODO without a ticket. | Search the added lines for print and log calls, `debugger`, `TODO` and commented-out code. |
| 11 | A comment only explains a why the code cannot show. | No new comment restates what its code does. Where a ticket, the brief or the user is the source of the why, the comment names it. |
| 12 | Do not touch code the task does not change: no reformatting, renames or lint fixes, no hand edits to generated files, no lockfile change without a dependency change. | Every changed file serves a step of the plan. |
| 13 | No new dependency unless the brief records it. | Compare manifest changes with the brief. A dependency it does not name: `ZED:QUESTION`. |
| 14 | Handle preconditions, errors and edge cases first, with an early return, `continue` or throw, so the main path is not nested. | No function the diff adds, or whose nesting the diff deepens, nests blocks more than 2 levels deep inside its body. |
| 15 | A function that returns a value does not change its arguments or object state. A function that changes state, such as a database write, a state change or a call that changes another service, is not named like a query (`get`, `calculate`, `is`, `has`). | For each new or renamed function, list what it changes. |
| 16 | A helper with one caller lives next to that caller, not in a shared `utils` or base module. | Each new function in a shared module has callers in more than one module. |
| 17 | Reuse: before adding a function, search the repo for one that already does it, and use it. | Name your search terms and paths and what they found. A new function that does what a found one does fails. |
| 18 | Security: no secret in code or logs; input validated where it enters; no query, command or path built by joining strings with input. | Search the diff for keys, tokens and passwords. For each place input enters, name its validation. Read every query, command and path built from input. |
| 19 | Accessibility (UI changes): interactive elements are native or have a role and a label; every input has a label; everything works by keyboard; errors are announced. | For each UI element in the diff, name its label and how a keyboard reaches it. Not applicable if the diff has no UI. |
| 20 | Resource cleanup: whatever the change opens (listener, timer, subscription, connection, file) is closed on every path. | For each open in the diff, name the close and show it runs on the error path too. |
| 21 | States and types: UI that loads data shows loading, empty and error states; in a typed language there is no `any` or other untyped escape, and public functions declare their return type. | For each async UI in the diff, name its three states. Run the type checker. |

## Examples

One wrong and one right case for each rule, by its number.

1. Bad: a rename of `getUser` in a file no plan step covers. Good: only what a plan step covers changes.
2. Bad: `throw new Error("missing")` in a module that throws `NotFoundError`. Good: `throw new NotFoundError("user", id)`, as the module does.
3. Bad: `validateAndSave(order)`. Good: `validate(order)`, then `save(order)`.
4. Bad: `const f = items.filter(i => i.s)`. Good: `const activeItems = items.filter(item => item.isActive)`.
5. Bad: `if (retries > 3)`. Good: `if (retries > MAX_RETRIES)`, with `MAX_RETRIES = 3` beside similar constants.
6. Bad: every internal function re-checks `if (!id) throw`. Good: the handler checks `id` once; internal functions trust it.
7. Bad: `try { save() } catch { return null }`. Good: `catch (e) { throw new SaveError("save order " + id + ": " + e.message) }`.
8. Bad: a new name limit of 50 in the form while the API still says 30. Good: form and API both use one `MAX_NAME_LENGTH`.
9. Bad: `export(items, { format = "csv", compress = false })` when only csv is used. Good: `export(items)`.
10. Bad: `console.log(order)` and `// old: calc(a)` left in the diff. Good: neither is in the diff.
11. Bad: `// loop over items`. Good: `// Rounds up: invoices bill per started hour (the brief, "Billing")`.
12. Bad: reformatting a file no plan step changes. Good: the diff touches only files a plan step needs.
13. Bad: adding `lodash` for one `chunk` call. Good: the standard library, or the brief records `lodash`.
14. Bad: `if (a) { if (b) { if (c) { run() } } }`. Good: `if (!a) return; if (!b) return; if (!c) return; run()`.
15. Bad: `getTotal(order)` also writes `order.total`. Good: `calculateTotal(order)` returns it; `saveTotal(order, total)` writes it.
16. Bad: `utils/formatInvoiceDate()` called from one module. Good: it lives beside that module.
17. Bad: a new `slugify()` when `text/slug` already has `toSlug()`. Good: call `toSlug()`.
18. Bad: `db.query("SELECT * FROM users WHERE id = " + id)`. Good: `db.query("SELECT * FROM users WHERE id = ?", [id])`.
19. Bad: `<div onclick="save()">Save</div>` and `<input placeholder="Name">`. Good: `<button type="button">Save</button>` and `<label for="name">Name</label><input id="name">`.
20. Bad: `const t = setInterval(poll, 1000)` and no `clearInterval`. Good: `clearInterval(t)` in the cleanup or a `finally`.
21. Bad: a list rendered with no loading, empty or error state, and `function total(x: any)`. Good: loading, empty ("No orders yet") and error states, and `function total(order: Order): number`.
