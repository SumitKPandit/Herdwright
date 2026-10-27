# Claude Code adapter facts

Verified 2026-10-27 on Claude Code 2.1.311 (herdr 0.9.5) with real sessions in a scratch repo: `claude -p`
(non-interactive) and an interactive session started by `herdr agent start --kind claude`. Reference: design
`employees.md` section 4 (rules R1-R6). Re-probe when the pinned version changes.

Verdicts: `yes` works as needed, `partial` works with the stated limit, `no` unavailable (use the fallback).

| Cell | Verdict | Verified | Evidence and how to use it | Fallback if no/partial |
|---|---|---|---|---|
| deny: per-worker hook settings | yes | 2026-10-27 | `claude --settings <file.json>` adds hooks for that session only and is **additive** to the project's `.claude/settings.json` (both fired). A `PreToolUse` command hook that exits 2 blocks the tool; stderr is shown to the model. The hook gets JSON on stdin (`tool_name`, `tool_input`, `session_id`, `cwd`). | none needed; git-level denial stays in `zed-guard` (R2) |
| identity: `ZED_EMPLOYEE`/`ZED_HAT` | yes | 2026-10-27 | `herdr workspace create --env K=V` reaches the agent: visible in a Bash tool call and in every hook's environment. | session id lookup in the DB |
| stop hook: write-back at turn end | yes | 2026-10-27 | `Stop` hook (also via `--settings`) runs at the end of each turn with the env above (already used by `bin/zed hook`). | `zed-employee finish` required by rules + watcher check (R3) |
| skills: native discovery from a directory | yes | 2026-10-27 | Two forms work. (1) `--add-dir <dir>` where `<dir>/.claude/skills/<name>/SKILL.md` exists: skill is listed and invocable (`/<name>` and the Skill tool). A plain `<dir>/skills/<name>` under `--add-dir` is **not** found. (2) `--plugin-dir <dir>` with `.claude-plugin/plugin.json` and `skills/<name>/SKILL.md`: invoked as `/<plugin>:<name>`. Gotcha: `--add-dir` is variadic and swallows a following prompt; put the prompt first or pass flags after it. | `zed-employee skill show <name>` (R4) |
| subagent cap: `Agent` matcher | yes | 2026-10-27 | A `PreToolUse` hook with `"matcher": "Agent"` fires on subagent spawns (`tool_name` is `Agent`; `tool_input` has `description`, `prompt`, `subagent_type`); exit 2 blocks with the stderr text. A counting cap needs a state file keyed by `session_id`. | rule + `report` flag |
| prompt to a *working* session | partial | 2026-10-27 | `herdr agent prompt` while the agent is `working` is **queued, not an interrupt**: the in-flight tool call finishes, then the message is injected at the next tool boundary and the model acts on it. The model may then drop its earlier plan (5-step job: step 1 finished, the new message ran, steps 2-5 never ran). | deliver at step boundaries (R5): send only to an `idle`/`done` session, or accept that the running plan may be abandoned |
| doorbell to an idle session | yes | 2026-10-27 | `herdr agent prompt` on an idle session starts a turn (also exercised by `bin/zed` already). | none needed |

## Other facts seen

- Auto-mode asks before reading files outside the working directories ("Read outside the working directories"):
  a probe that reads scripts outside the worktree blocks on a dialog. Keep helper files inside the worktree or
  under an `--add-dir`.
- Claude backgrounds a foreground `sleep` and long commands; it can refuse a bare long `sleep`. Use a script
  for timing probes.
- First start in a new folder shows the trust dialog; `bin/zed` already handles it.
