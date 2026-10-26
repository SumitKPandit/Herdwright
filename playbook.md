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
- **Usage limits are shared.** Zed and every worker (and their sub-agents) draw on the same
  claude.ai plan limit. Hitting it pauses everything until reset (workers auto-continue after the
  reset; sub-agents may need a restart). Size parallel waves to the plan, tell the user when a
  limit pauses work, and after a reset check that each worker actually resumed.
