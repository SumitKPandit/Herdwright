# <Name> — profile

<!-- Who the user is and how they want Herdwright to work. Herdwright reads this at the start of every session.
     Filled in during onboarding; keep it current (the `learn` skill). Facts only; mark anything
     inferred as "(inferred, unconfirmed)". -->

## Who
- Name: <name>. Role: <role> at <organisation>, team/product: <team>.
- Time zone / working hours: <...>

## Accounts and tools
<!-- One line per account Herdwright or its workers use: what it is, the handle, how it's connected,
     how to verify it, and when it was last verified. Never put secrets here. -->
- GitHub: `<handle>` via `gh` (verify: `gh auth status`). Verified <date>.
- <ticket tracker / wiki / chat / ...>: <handle>, connected via <MCP server /
  CLI / Keychain item `<service>`>, verify with <command>. Verified <date>.

## How they work
- <communication preferences: how short, how often, what to lead with>
- <which decisions they want to make themselves vs. leave to Herdwright>

## Herdwright housekeeping
- Profile branch: `profile/<name>`; pushed to `<remote>` (<url>) — a private remote the user chose.
- Core changes: <push `main` to `<remote>` after `bin/hw-core-sync --apply` / propose them to the user first>.
- Commit message format in this repo: <e.g. "HW | Description in imperative mood">.
