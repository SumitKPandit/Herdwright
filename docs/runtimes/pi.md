# Pi adapter facts

Verified 2026-10-27 on Pi 1.4.0 (herdr 0.9.3) with real sessions in a scratch repo: `pi -p`
(non-interactive) and an interactive session started by `herdr agent start --kind pi`.
Reference: design `employees.md` section 4 (rules R1-R6). Re-probe when the pinned version changes.

Verdicts: `yes` works as needed, `partial` works with the stated limit, `no` unavailable (use the fallback).

| Cell | Verdict | Verified | Evidence and how to use it | Fallback if no/partial |
|---|---|---|---|---|
| deny: per-worker hook settings | yes | 2026-10-27 | Two routes. (1) `--exclude-tools <tools>` disables tools by name or pattern. (2) Extensions: `--extension <file.ts>` loads an extension registering `pi.on("tool_call", ...)` which inspects `toolName` and `input`, returning `{ block: true, reason: ... }` to block execution deterministically. Stdin JSON for `zed-deny` carries `tool_name`, `tool_input`, `cwd`. | none needed; git-level denial stays in `zed-guard` (R2) |
| identity: `ZED_EMPLOYEE`/`ZED_HAT` | yes | 2026-10-27 | `herdr workspace create --env K=V` reaches the shell environment (`process.env`), child bash processes, and extensions. | session id lookup in the DB |
| stop hook: write-back at turn end | yes | 2026-10-27 | Pi extensions register `turn_end` / `agent_end` event handlers, running `zed-employee stop-guard` at the end of every turn. | `zed-employee finish` required by rules + watcher check (R3) |
| skills: native discovery from a directory | yes | 2026-10-27 | Pi natively discovers Agent Skills specification directories in `.agents/skills/` and `--skill <dir>` (`SKILL.md` with frontmatter `name` and `description`). `.agents/skills` symlinks to `.claude/skills`. | `zed-employee skill show <name>` (R4) |
| subagent cap: `Agent` matcher | yes | 2026-10-27 | Pi has no subagent tool enabled unless configured; custom tools or subagent extensions are intercepted in `tool_call` and blocked if not permitted. | rule + `report` flag |
| prompt to a *working* session | partial | 2026-10-27 | Same as Claude and Copilot: `herdr agent prompt` to a `working` agent queues or injects at turn boundary, which may drop the rest of the in-flight plan. | deliver at step boundaries (R5): send only to an `idle`/`done` session |
| doorbell to an idle session | yes | 2026-10-27 | `herdr agent prompt <TARGET> <TEXT>` on an idle session starts a new turn and wakes the agent. | none needed |

## Other facts seen

- Pi runs with `--approve` to trust project-local files without interactive prompt.
- `--append-system-prompt` appends custom instructions or worker rules file.
- Thinking level is controlled with `--thinking <level>` (`off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`).
- Model is controlled with `--model <pattern>`.
