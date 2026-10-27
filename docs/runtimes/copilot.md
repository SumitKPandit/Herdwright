# Copilot CLI adapter facts

Verified 2026-10-27 on GitHub Copilot CLI 1.0.103 (herdr 0.9.5) with real sessions in a scratch repo: `copilot -p`
(non-interactive, `--model auto`) and an interactive session started by `herdr agent start --kind copilot`.
Reference: design `employees.md` section 4 (rules R1-R6). Re-probe when the pinned version changes.

Verdicts: `yes` works as needed, `partial` works with the stated limit, `no` unavailable (use the fallback).

| Cell | Verdict | Verified | Evidence and how to use it | Fallback if no/partial |
|---|---|---|---|---|
| deny: per-worker hook settings | yes | 2026-10-27 | Two routes. (1) `--deny-tool='shell(echo)'` denies without prompting (also in `bin/zed`). (2) Hooks: `--plugin-dir <dir>` with `plugin.json` and `hooks/hooks.json` (`{"version":1,"hooks":{"PreToolUse":[{"type":"command","bash":"<cmd>","timeoutSec":10}]}}`) loads hooks for that session only, with no repo or user file touched. Event names are **PascalCase** (`PreToolUse`, `Stop`, `SessionEnd`, `SubagentStop`); lowercase names (`preToolUse`, `agentStop`) did **not** fire. A hook denies by printing `{"permissionDecision":"deny","permissionDecisionReason":"..."}` or by exiting 2. A hook that errors (e.g. missing script) also denies (fail closed). Stdin JSON has `tool_name` (`Bash`, `Agent`, `skill`), `tool_input`, `session_id`, `cwd`. Repo-level `.github/hooks/*.json` works too but needs a trusted folder and pollutes the worktree. | `zed-guard` (git) + watcher + worker rule |
| identity: `ZED_EMPLOYEE`/`ZED_HAT` | yes | 2026-10-27 | `herdr workspace create --env K=V` reaches a shell tool call (`EMP=quill HAT=review`) and every hook's environment. | session id lookup in the DB |
| stop hook: write-back at turn end | yes | 2026-10-27 | A `Stop` hook runs at the end of each turn, in `-p` and interactive. Printing `{"decision":"block","reason":"..."}` makes Copilot continue and do what the reason says; the next `Stop` has `stop_hook_active: true` (guard against loops). This contradicts the design's "none known"; `zed-employee finish` can be enforced like Claude's. | `zed-employee finish` + watcher check on `ZED:DONE` (R3) |
| skills: native discovery from a directory | yes | 2026-10-27 | `--add-dir <dir>` loads `<dir>/.github/skills/<name>/SKILL.md` (a `.claude/skills` under `--add-dir` was **not** found). `--plugin-dir <dir>` with `skills/<name>/SKILL.md` works. Skills are invoked by the `skill` tool (model-chosen) or by `/<plugin>:<name>`; plain `/<name>` for a plugin skill is "Unknown command". Project `.github/skills`, `.agents/skills`, `.claude/skills` are found from the working directory (per `copilot skill --help`; not separately probed). | `zed-employee skill show <name>` (R4) |
| subagent cap: `Agent` matcher | yes | 2026-10-27 | Subagent spawns reach `PreToolUse` with `tool_name` `Agent` (`tool_input`: `description`, `prompt`, `agent_type`); a deny reason is returned to the model ("Denied by preToolUse hook: ..."). A counting cap needs a state file keyed by `session_id`. | rule + `report` flag |
| prompt to a *working* session | partial | 2026-10-27 | Same as Claude: `herdr agent prompt` to a `working` agent is **queued, not an interrupt**; the running tool finishes, the message is injected at the next tool boundary, and the model then acts on it (steps 1-2 ran, the new message ran, steps 3-5 never ran). | deliver at step boundaries (R5): send only to an `idle`/`done` session |
| doorbell to an idle session | yes | 2026-10-27 | `herdr agent prompt` on an idle session starts a turn (as `bin/zed` already does). | none needed |

## Other facts seen

- Non-interactive runs need `--allow-tool=...` (or an allow-all flag); prefer a narrow allow list.
- Interactive default is Manual Approval: redirects (`>`) and unlisted commands raise an approval dialog and the
  session shows `blocked` until answered.
- Copilot may merge several commands into one tool call; give explicit "one call each" instructions in probes.
- `--model auto` is accepted; an unavailable model name aborts with `Model "..." from --model flag is not available.`
- `--model auto` works only with `-p`. In an interactive session it never finishes loading (footer `Loading: ... waiting on
  extensions; ide`) and the first prompt stays queued; leave the model unset or name one (end-to-end run).

## Hat deny policy as built (verified 2026-10-27, Copilot CLI 1.0.103)

`spawn employee= hat=` writes a per-session plugin `profile/.cache/employees/<name>/<agent>-plugin/` (`plugin.json`,
`hooks/hooks.json` with a PascalCase `PreToolUse` hook running `bin/zed-deny`) and starts Copilot with `--plugin-dir`
plus one `--deny-tool=shell(<prefix>)` per denied command. A real `copilot -p` session was denied both a file creation
and `gh pr comment 1 --body hi` ("Denied by preToolUse hook: hook exited with code 2"); `Changes +0 -0`.
Copilot's edit tool arrives at the hook as `tool_name` `Edit` with `tool_input` a **string** in apply_patch format
(`*** Add File: b.py` ...), not a JSON object: `zed-deny` reads the paths from the `*** Add|Update|Delete File:` and
`*** Move to:` lines. Edits through the shell are caught by the `Bash` rule (redirects, rm/mv/cp/tee, sed -i).
Not caught on either runtime: a program that writes files itself (e.g. `python -c`); git stays guarded by `zed-guard`.

Limits of the shell check (both runtimes; it is best-effort, git itself is guarded by zed-guard): it follows `cd <dir>` inside one
command line, but a write through an interpreter (`python3 -c "open(...)"`) is not seen, and a write target the shell would expand
(`$VAR`, a backtick, `~user`) is denied as "cannot be checked": use a literal absolute path.
