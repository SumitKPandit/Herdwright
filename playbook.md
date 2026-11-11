# Playbook — lessons for every Zed

Core lessons about tools and workflow. Facts about a user or their work go to the profile (`learn` skill).
Anything a script or hook can enforce is code, not a line here.

- **macOS Keychain: never paste a long secret at the interactive prompt.** It truncates at 128 characters, so
  API tokens end up wrong (401s). Pass it inline: `security add-generic-password -U -s <service> -a <account> -w "$(pbpaste)"`.
- **Auto-mode may block pushing employer data to personal remotes.** Keep work data in the profile and push the profile
  branch only to a remote the user confirmed; if a push is blocked, tell the user instead of working around it.
- **`timeout` isn't available on macOS.** Use the tool's own timeout flag, Monitor's timeout, or
  `perl -e 'alarm 60; exec @ARGV' …`.
- **Core and profile changes go in separate commits**, so `bin/zed-core-sync` can carry core-only commits to `main`.
  Stage by path and commit twice when a change spans both.
- **Sleep (macOS):** a working Claude session holds `caffeinate -i`. Lid-close, Sleep or a flat battery still pauses
  everything and in-flight API calls may fail: after a wake run `check-workers` and nudge workers that stopped on an error.
- **Usage limits are shared.** Zed and every worker (and sub-agent) draw on the same Claude plan, so a limit pauses all of
  them until it resets. Size parallel waves to the plan, tell the user when a limit pauses work, and after a reset check
  that each worker actually resumed.
- **A one-shot watcher event is work to handle, not monitoring left running.** Inspect every affected worker, resolve
  already-authorized routine requests or surface the exact decision, verify the state, then make sure exactly one watcher
  is armed while tracked work remains. Keep polling inside the background process; scheduled chat prompts are not a watchdog.
- **A worker's permission prompts can queue, and the watcher reports a block only once.** After each approval read the
  screen again until the agent is `working` with no prompt showing. Read each prompt before answering; never send a blind
  "don't ask again". Send approvals as a single standalone `herdr agent send-keys <agent> <key>`.
- **Do not approve regex-looking root paths.** A search pattern with a leading slash can show up as an absolute access
  path in a permission dialog: decline it and have the worker correct the query.
- **Native CLI commands are text, not keys.** `send-keys` rejects `/compact`; use `herdr agent prompt <agent> /compact`
  without `--wait` at verified idle, and confirm the context fell before resuming.
- **Herdr can report `agent_prompt_stalled` before the CLI starts a submitted prompt.** Inspect its state and
  state-change sequence; never submit the same brief again. For a stuck startup, `bin/zed close <S> keep-worktree`
  preserves unlanded work. Verify real tool/plan activity, not a `Working` label.
- **A replacement worker should resume the work, not rebuild the setup.** Say so in the first prompt: trust recorded
  state, change tooling only when a run fails, start producing quickly, scripts for mechanical steps, report throughput early.
- **Claude Stop hooks: `asyncRewake` wakes the session only when the hook exits 2 with events on stderr.** A hook that always
  exits 2 loops forever. Exit 2 only for new events, keep the hook single-flight (one watcher, an `flock`), and keep un-acked
  events in a durable queue.
- **Never run an unbounded test or experiment.** Run one test file at a time under a time limit, use throwaway
  `ZED_PROFILE_DIR`s, and stop your own background runs by PID or `TaskStop`, never `pkill -f` on a pattern other sessions match.
- **Keep coordinator QA separate from worker fixtures.** Workers correctly refuse coordinator lock and watcher duties:
  launch a separate, explicitly scoped coordinator fixture with the throwaway profile, and re-read its screen before any action.
- **PR review posting.** `gh pr review <n> -R <owner>/<repo> --approve -b "<text>"` prints nothing on success; verify with
  `gh pr view --json reviews`. No raw REST calls. Relay the user's OK to post with
  `bin/zed relay-post` (their words verbatim plus the exact action).
- **Review and QA of gated work go to their own sessions** (`spawn … hat=review|qa`); a session that changed the code gives no
  verdict. Verdicts are about the code outside `profile/`, so a later profile-only commit keeps them. Never turn the gate off to publish.
