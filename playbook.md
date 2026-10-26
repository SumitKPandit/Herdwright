# Playbook — lessons for every Zed

Core lessons about tools and workflow that make any Zed a better assistant, whoever its user is.
Facts about a particular user or their work never go here (they go to the profile; see the
`learn` skill). Each entry: the lesson, **Why**, **How to apply**. Newest last.

- **macOS Keychain: never paste a long secret at the interactive prompt.**
  `security add-generic-password ... -w` with no value prompts, and that prompt silently truncates
  input at 128 characters. **Why:** API tokens (e.g. Atlassian) are longer; the stored token is
  then wrong and auth fails with 401s. **How:** copy the token and pass it inline:
  `security add-generic-password -U -s <service> -a <account> -w "$(pbpaste)"`.
- **Fresh worktrees trigger Claude Code's folder-trust dialog.** **Why:** a worker started in a
  new directory waits on "Do you trust this folder?" and never reads its brief. **How:**
  `bin/zed spawn` accepts the dialog automatically for worktrees Zed itself created from a
  configured repo. Anything else that blocks a worker stays blocked for Zed to inspect and ask.
- **Auto-mode may block pushing third-party or employer data to personal remotes.** **Why:** the
  permission classifier treats it as possible data exfiltration. **How:** keep a user's work data
  in their profile and push the profile branch only to a remote the user has confirmed is the
  right home for it; if a push is blocked, tell the user rather than working around it.
- **`timeout` isn't available on macOS.** **Why:** it's GNU coreutils. **How:** use the tool's
  own timeout flag (e.g. `herdr ... --timeout <ms>`), or the Monitor tool's timeout for long waits.
- **Keep core and profile changes in separate commits.** **Why:** `bin/zed-core-sync` can only
  carry commits that touch nothing under `profile/` to `main`. **How:** stage by path
  (`git add profile/` vs. everything else) and commit twice when a change spans both.
- **Sleep (macOS):** each Claude Code session runs `caffeinate -i` while it's working, so idle
  sleep is already prevented whenever any worker (or Zed) is mid-turn. Closing the lid, choosing
  Sleep, or a flat battery still sleeps the machine; everything pauses and resumes on wake, but
  in-flight API calls may fail — after a wake, run `check-workers` and nudge any worker that
  stopped on an error. To survive lid-close, the Mac must be on power with an external display
  (clamshell) — don't change system power settings without the user's OK.
- **Usage limits are shared per runtime.** Zed and every worker (and their sub-agents) on the same
  runtime draw on the same plan: all Claude sessions share the claude.ai plan limit, all Copilot
  sessions share the Copilot plan's AI credits. Hitting a limit pauses everything on that runtime
  until reset (Claude workers auto-continue after the reset; sub-agents may need a restart). Size
  parallel waves to the plan, tell the user when a limit pauses work, and after a reset check that
  each worker actually resumed. With both runtimes, see the `switch-runtime` skill.
- **Copilot CLI: never autopilot.** **Why:** in autopilot, Copilot's ask-the-user tool doesn't
  wait; it returns "Making best guess on autopilot", which breaks the no-assumption rule. **How:**
  Zed and its workers run `--mode interactive`. Workers are told to ask only via ZED:QUESTION.
- **Copilot CLI wakes on a detached shell's completion.** **Why:** there is no Monitor tool; a
  background shell started detached sends a "Detached shell … completed" notification that starts
  a new turn even when the session is idle. **How:** Zed under Copilot runs `bin/zed watch --once`
  detached and re-arms it after each wake-up.
- **Copilot CLI workers need their rules and paths passed explicitly.** **Why:** there's no
  system-prompt flag, and file access is limited to the working directory. **How:** `spawn` writes
  the worker rules to `<profile>/.cache/copilot-instructions/zed-worker.instructions.md`, passes
  that folder in `COPILOT_CUSTOM_INSTRUCTIONS_DIRS` (via `herdr workspace create --env`), and adds
  `--add-dir` for the profile and Zed's home. Its folder-trust dialog ("Do you trust the files in
  this folder?") is accepted for worktrees Zed created.
- **Copilot's Assisted Permissions judge the conversation, not the brief.** **Why:** the reviewer
  approves what the human asked for in the session's messages. It escalated a worker's file writes,
  commits and even `printf` checks because the authority sat in the brief file. It also approved an
  explicitly requested `git push --force`. **How:** it isn't a substitute for the worker rules or the
  git guards. `--deny-tool` (`deny_tools` in `runtimes.json`) is the only hard block; expect
  approval dialogs (herdr `blocked`) and relay them to the user. Interactive sessions can only enter
  Assisted mode through `/permissions` (the `--assisted-approval` flag is prompt-mode only), and
  `spawn` does that.
- **Claude Code's auto mode may refuse to launch unrestricted agents.** **Why:** its safety check
  treats starting another agent with all permissions (e.g. `copilot --allow-all-tools`), or wording
  a prompt to pre-authorise another agent's permission reviewer, as unsafe. **How:** don't work
  around it; ask the user, who can choose the permission mode in `runtimes.json` or add a
  permission rule.
- **Herdr can report `agent_prompt_stalled` before Copilot starts a submitted prompt.** **Why:**
  the activity gate expires after five seconds, but the prompt may already be in the transcript
  and start shortly afterwards. **How:** inspect the agent's state and state-change sequence;
  `spawn` allows a short observation window. Never blindly submit the same brief again.
- **Keep coordinator QA separate from worker fixtures.** **Why:** workers correctly refuse
  coordinator lock and watcher duties, even in a throwaway profile. **How:** launch a separate,
  explicitly scoped coordinator fixture with its own instructions and the throwaway profile.
  Before any UI action, re-read the agent's current state and screen; never answer a stale dialog.
- **Tool-call shells are not persistent coordinator identities.** **Why:** a terminal tool may
  start a fresh shell for each call; a lock owned by that shell becomes stale when it exits.
  **How:** use the runtime's persistent session identity. With `bin/zed`'s legacy PID fallback,
  set `CLAUDE_PID` to the verified long-lived CLI process for every lock-protected command and
  watcher. Verify ownership again from a separate tool call.
- **A one-shot watcher event is work to handle, not monitoring left running.** **Why:** the
  watcher exits after delivering a checkpoint; replying to another message without processing
  it and re-arming leaves workers silently paused. **How:** inspect every affected worker,
  resolve already-authorized routine requests or surface the exact decision needed, verify the
  resulting state, then re-arm exactly one watcher while tracked work remains. Check that it
  is waiting, not merely that it printed a startup line before exiting with another event.
  Where supported, an independent scheduled supervision check can catch missed callbacks;
  it must verify lock ownership and stop when the coordinator loses the role or work ends.
