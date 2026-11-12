# Herdwright

Herdwright is a personal engineering assistant that runs on [Claude Code](https://claude.com/claude-code) or on
[OpenCode](https://opencode.ai). You talk to one session, the coordinator. It records each request as a task, and hands
the work to worker sessions in [herdr](https://herdr.dev), each in its own git worktree with a written brief. It brings
their questions to you and your answers back to them, and keeps tasks, sessions, questions and answers in a small
database in your profile.
<!-- test: test_e2e_loop.py::Record.test_a_task_is_recorded; test_e2e_loop.py::Spawn.test_a_worker_starts_in_its_own_worktree_with_its_brief; test_e2e_loop.py::Watch.test_a_question_is_reported_once_until_acknowledged; test_e2e_loop.py::Relay.test_an_answer_is_relayed_verbatim_and_recorded_and_a_working_worker_is_not_interrupted -->

Every statement in this file about what Herdwright does names the automated test that backs it (in the source of this
file, after the statement), and `tests/test_readme_claims.py` fails when a statement has none. Where something is an
instruction to a model, an observation or a limit rather than a tested mechanism, it says so.
<!-- test: test_readme_claims.py::ReadmeClaims.test_every_block_is_backed_by_a_test_or_says_what_else_it_is -->

## Who it is for

Someone who leads engineering work in one or more git repositories and wants to hand tasks to AI coding sessions
without watching each terminal: you decide and answer, the sessions build, review and test. It is one person's
assistant, not a team server: one profile, one coordinator.
<!-- note: who it is meant for -->

## What it does

- **No worker starts without a brief and a plan**, and each worker gets its own worktree.
  <!-- test: test_e2e_loop.py::Brief.test_no_worker_starts_without_a_brief; test_e2e_loop.py::Brief.test_no_worker_starts_on_a_brief_with_unfilled_spots; test_e2e_loop.py::Brief.test_no_worker_starts_on_a_brief_without_a_plan; test_e2e_loop.py::Spawn.test_a_worker_starts_in_its_own_worktree_with_its_brief -->
- **A worker's question reaches you once, and your answer reaches the worker in your words**, also when the worker is
  busy: it reads the answer between steps, or is prompted with it once it is idle. A question it asks again in the same
  words gets the recorded answer, and you are not asked twice.
  <!-- test: test_e2e_loop.py::Watch.test_a_question_is_reported_once_until_acknowledged; test_e2e_loop.py::Relay.test_an_answer_is_relayed_verbatim_and_recorded_and_a_working_worker_is_not_interrupted; test_e2e_loop.py::Answers.test_a_worker_that_asked_and_kept_working_reads_its_answer_without_being_interrupted; test_e2e_loop.py::Answers.test_an_answer_a_busy_worker_did_not_read_is_prompted_once_when_it_is_idle; test_e2e_loop.py::Answers.test_a_question_asked_again_is_answered_from_the_record_and_not_put_to_the_user_again -->
- **Workers wear a hat: build, review or qa.** Only a build session may change the code; one build session writes a
  worktree at a time; a review or QA that changed the code gives no verdict.
  <!-- test: test_hw_deny.py::DecideTests.test_only_build_edits_the_worktree_and_the_profile_config; test_hw_assign.py::SpawnTests.test_a_build_is_refused_while_a_reader_is_open_and_readers_may_share; test_hw_finish.py::FinishTests.test_a_review_that_changed_the_code_is_its_author_and_gives_no_verdict; test_e2e_loop.py::Merge.test_a_reviewer_that_changed_the_code_gives_no_verdict -->
- **Review and QA start only when the build's plan is complete**, and a session finishes only with its own plan ticked.
  <!-- test: test_hw_assign.py::SpawnTests.test_review_and_qa_wait_for_a_complete_build_plan; test_hw_finish.py::FinishTests.test_a_build_with_an_unticked_plan_step_is_refused_until_it_is_ticked -->
- **A merge waits for a clean review and a clean QA** by sessions that did not write the code, in `bin/hw merge` and in
  the git hooks; your override is possible and is logged.
  <!-- test: test_e2e_loop.py::Merge.test_a_merge_waits_for_a_clean_review_and_qa; test_hooks.py::ReferenceTransaction.test_unreviewed_work_is_not_merged; test_hooks.py::ReferenceTransaction.test_work_that_changed_after_its_verdicts_is_not_merged; test_hooks.py::ReferenceTransaction.test_the_users_override_is_logged -->
- **A worker that reports done without QA is flagged**, and a session is closed and its worktree removed only when its
  work has landed.
  <!-- test: test_e2e_loop.py::Watch.test_a_done_without_qa_is_flagged; test_e2e_loop.py::Close.test_closing_refuses_unlanded_work; test_e2e_loop.py::Close.test_closing_removes_a_landed_worktree -->
- **The coordinator does not do the work itself**: a guard stops its edits, commits and file-writing shell commands in
  your project repositories.
  <!-- test: test_hw_pretool.py::PretoolTests.test_edits; test_hw_pretool.py::PretoolTests.test_git_writes; test_hw_pretool.py::PretoolTests.test_file_writers -->
- **Workers cannot post outward**: outward commands and the tools listed under `worker.deny_tools` are denied to them,
  and the profile template lists the write tools of one tracker and wiki connector.
  <!-- test: test_hw_deny.py::DecideTests.test_outward_commands_are_denied_by_prefix_whatever_the_program_path_or_env; test_hw_assign.py::RenderedPolicyTests.test_claude_settings_deny_the_tools_the_profile_names; test_hw_release.py::TemplateDenyTests.test_a_session_built_from_the_template_denies_the_tracker_and_wiki_write_tools -->
- **One coordinator per profile**: a second session cannot take the profile's lock silently.
  <!-- test: test_e2e_loop.py::Lock.test_a_second_coordinator_cannot_take_the_lock_silently -->

The coordinator's manual (`AGENTS.md`) also tells the model to ask instead of assuming, to show you the exact text
before anything goes to a tracker, wiki, chat or PR comment, and to write down what you teach it. Those are instructions
to a model. The tests show the manual says them; no test can promise a model follows them every time.
<!-- note: instructions to a model, not mechanisms -->

## What you need

- [herdr](https://herdr.dev), `git`, the GitHub CLI `gh` and `python3`. `bin/hw-setup` names each one that is missing.
  <!-- test: test_hw_setup.py::SetupCase.test_each_missing_tool_is_named_and_fails_the_run -->
- One runtime: Claude Code (`claude`) or OpenCode (`opencode`). An install uses one of them, for the coordinator and
  the workers alike.
  <!-- test: test_hw_setup.py::SetupCase.test_one_runtime_is_enough_and_none_fails_the_run; test_hw_coordinator.py::RuntimeTests.test_a_worker_runs_on_the_installs_runtime_unless_spawn_names_one -->
- Herdwright was developed and tested on macOS, with Python 3.14 and OpenCode v2.2.7. Nothing else has been tried.
  <!-- note: where it was tested -->

## Install

1. Install the tools above and log in to your runtime and to GitHub (`gh auth login`).
   <!-- steps -->
2. Clone Herdwright and run its setup:
   `git clone https://github.com/SumitKPandit/Herdwright.git ~/herdwright && cd ~/herdwright && bin/hw-setup`
   <!-- steps -->
3. Read what setup printed. It checks the tools, installs Herdwright's git guard hooks in this clone, and settles the
   runtime: the one that is installed. With both or neither installed it asks which one you use; outside a terminal it
   tells you to run `bin/hw-setup --runtime claude|opencode`. Running it again changes nothing.
   <!-- test: test_hw_setup.py::SetupCase.test_a_complete_machine_passes_and_gets_the_guard_hooks; test_hw_setup.py::SetupCase.test_with_both_runtimes_installed_it_asks_in_a_terminal_which_one_and_stores_the_answer; test_hw_setup.py::SetupCase.test_with_both_runtimes_and_no_answer_nothing_is_chosen_and_the_run_fails_until_one_is_named; test_hw_setup.py::SetupCase.test_running_it_again_changes_nothing -->
4. Start herdr, open a pane in the Herdwright folder and start the coordinator: `claude` on Claude Code,
   `bin/hw coordinator` on OpenCode.
   <!-- steps -->
5. Say hello. The session runs `bin/hw start`, which finds no profile and sends it to onboarding: it asks who you are,
   which repositories and tools you use and how you work, and creates your profile on your own branch,
   `profile/<name>`.
   <!-- test: test_hw_rename.py::OldNamesInACheckout.test_a_new_user_with_no_old_variable_is_still_sent_to_onboarding; test_hw_coordinator.py::StartTests.test_a_new_user_is_sent_to_onboarding_and_nothing_else_happens -->

Setup asks nothing about models: see "Model, effort and sessions".
<!-- test: test_hw_setup.py::SetupCase.test_it_asks_nothing_about_models_and_writes_none -->

## Your first task

After onboarding your profile knows at least one repository. Then:
<!-- steps -->

1. Ask for something small in it, for example: "In `<repo>`, add a line to the README that says who maintains it. Base
   branch `main`."
   <!-- steps -->
2. The coordinator records a task and writes a brief with a plan section; it asks you for anything the brief needs and
   does not have.
   <!-- test: test_e2e_loop.py::Record.test_a_task_is_recorded; test_hw.py::HwTests.test_brief_scaffold_and_spawn_refuses_placeholders -->
3. It starts a worker: a new herdr workspace opens in a fresh worktree of your repository, on the task's branch, and
   the worker gets its brief as its first prompt.
   <!-- test: test_e2e_loop.py::Spawn.test_a_worker_starts_in_its_own_worktree_with_its_brief -->
4. The worker writes its plan file first and works through it. If it asks something, the coordinator puts the question
   to you with a number (`Q1`); answer in your own words.
   <!-- test: test_e2e_loop.py::Watch.test_a_question_is_reported_once_until_acknowledged; test_e2e_loop.py::Relay.test_an_answer_is_relayed_verbatim_and_recorded_and_a_working_worker_is_not_interrupted -->
5. When the worker reports done, the coordinator tells you what was done and how it was checked.
   <!-- test: test_e2e_loop.py::Watch.test_a_done_is_reported_once_and_the_session_closed -->
6. Ask "what is in progress?" at any time: the answer comes from the database (`bin/hw status`), not from the model's
   memory.
   <!-- test: test_hw.py::HwTests.test_brief_status_is_bounded_and_start_prints_a_digest; test_hw_assign.py::WatcherAndStartTests.test_the_digest_and_status_show_each_session_with_its_own_plan_and_hat -->

## Model, effort and sessions

Herdwright ships no model, no effort and no variant, and never picks one for you.
<!-- test: test_hw_configure.py::TemplateTests.test_the_template_ships_no_model_effort_or_variant; test_hw_configure.py::TemplateTests.test_no_model_is_written_into_the_code_as_a_fallback -->

- **Claude Code.** With nothing set, a worker is started without a model or an effort, so it runs on the default of
  your own Claude Code settings.
  <!-- test: test_hw_configure.py::OnClaudeCode.test_with_nothing_set_a_worker_is_started_without_model_or_effort_so_claude_codes_own_default_applies -->
- **OpenCode.** OpenCode's server has no default that is yours: a session created through it without a model ran a
  model the server picked, not the one the OpenCode TUI last used (observed on v2.2.7). So the first
  `bin/hw coordinator` asks once which model and variant to use, offers the model your TUI last used as the answer
  Enter accepts, and stores your answer. Without an answer it does not start, and no worker starts without a model.
  <!-- test: test_hw_coordinator.py::ModelQuestionTests.test_enter_accepts_the_model_and_variant_the_tui_last_used_and_they_are_stored; test_hw_coordinator.py::ModelQuestionTests.test_a_typed_model_wins_and_its_variant_is_not_guessed_from_another_model; test_hw_coordinator.py::ModelQuestionTests.test_without_an_answer_or_a_terminal_nothing_is_guessed_and_the_coordinator_does_not_open; test_hw_assign.py::OpenCodeTests.test_without_a_model_an_opencode_worker_is_refused_because_its_server_would_pick_one -->
- **Changing it.** Tell the coordinator which model, which effort (on OpenCode: variant) or how many sessions at once
  you want. Its manual tells it to store that with `bin/hw configure` and to tell you what was stored. The command
  writes your profile's config, and every worker started after it uses it, until you change it again;
  `--clear` drops a choice.
  <!-- test: test_hw_repo_instructions.py::ToolInsideItselfTests.test_the_manual_and_onboarding_leave_model_effort_and_sessions_to_the_user_and_name_the_command; test_hw_configure.py::OnClaudeCode.test_what_the_user_asked_for_is_stored_confirmed_and_used_by_every_later_worker; test_hw_configure.py::OnOpenCode.test_what_the_user_asked_for_is_stored_confirmed_and_used_by_the_next_worker; test_hw_configure.py::OnClaudeCode.test_clearing_a_choice_goes_back_to_claude_codes_own_default -->
- **Sessions at once.** The limit counts every open session of your runtime in herdr, the coordinator's and your own
  included, because they draw on the same plan; a worker beyond it is not started and its task stays queued. The
  template sets 3 for Claude Code. Not set: no limit.
  <!-- test: test_hw.py::HwTests.test_max_running_counts_open_sessions_of_the_runtime_and_queues_the_rest; test_hw_configure.py::TemplateTests.test_the_template_ships_no_model_effort_or_variant -->
- `bin/hw start` shows what workers run on at the start of every session.
  <!-- test: test_hw_configure.py::StartSaysWhatWorkersRunOn.test_start_names_the_choices_and_a_new_limit_shows_on_the_next_start -->

The coordinator's own model is whatever its session runs: on Claude Code the model you start `claude` with, which
workers do not inherit; on OpenCode the stored model.
<!-- note: observed with Claude Code; the OpenCode half is tested -->
<!-- test: test_hw_coordinator.py::KitTests.test_the_config_dir_holds_model_variant_permissions_instructions_and_the_guard -->

## What the guards are, and what they are not

A guard is a check before each tool call and each plain shell command. It stops a session doing by mistake what its
role does not allow: a worker what its hat denies, the coordinator a change in a project repository.
<!-- test: test_hw_deny.py::HookScriptTests.test_exit_2_with_the_reason_on_stderr_for_a_denied_call; test_hw_pretool.py::PretoolTests.test_only_the_lock_holder_is_constrained -->

A write is checked wherever its path points: absolute, relative to the call's working directory, or after a `cd`;
behind a compound command's opener or a wrapper (`{`, `then`, `do`, `env`, `sudo`, `xargs`, `timeout`); as any redirect
(`>`, `&>`, `>&`, after a heredoc), a file-writing program, `dd of=` or a deleting `find`.
<!-- test: test_hw_deny.py::DecideTests.test_cd_in_the_command_line_moves_where_relative_targets_resolve; test_hw_deny.py::DecideTests.test_an_opener_or_a_wrapper_before_a_write_does_not_hide_it; test_hw_deny.py::DecideTests.test_every_redirect_and_pipe_operator_is_read; test_hw_deny.py::DecideTests.test_writes_that_are_not_a_file_writers_first_word; test_hw_pretool.py::PretoolTests.test_a_cd_before_a_write_is_followed -->

**The guards are not a sandbox.** They do not stop a session that deliberately routes around them through another
program: a script that writes files itself (`python3 -c …`), and on OpenCode the server's terminal route (`/api/pty`,
with the server password any process of the same user can read from the process list) or a server the worker starts
itself. A path the shell expands (`$DIR/x`) is not followed: a read-only hat is refused such a write because it cannot
be checked.
<!-- note: the stated limit of the guards -->
<!-- test: test_hw_deny.py::DecideTests.test_targets_the_shell_would_expand_cannot_be_checked -->

Review and QA by sessions that did not write the code, and the git hooks, do not depend on these checks.
<!-- test: test_hooks.py::ReferenceTransaction.test_unreviewed_work_is_not_merged; test_hw_gates.py::MergeGateTests.test_a_review_that_changed_the_code_is_not_independent -->

## On OpenCode

- **Use a paid or subscription model.** During testing the free OpenCode models hit an account-wide usage limit (HTTP
  429) and every session stopped. A turn the provider refuses is reported with its error, but nothing runs until the
  limit lifts.
  <!-- note: observed during testing on 2026-11-11 -->
  <!-- test: test_hw_assign.py::OpenCodeTests.test_a_turn_the_provider_refused_is_reported_with_its_error -->
- **The mechanisms are tested; the model's obedience is not guaranteed.** Spawning, guards, the lock, the watcher and
  relaying are code and are tested. Whether a model follows the manual is up to the model: in one trial a free model
  followed a strict five-step order in 2 of 3 runs.
  <!-- note: observed during testing; a statement of what is not promised -->
- The coordinator is started with `bin/hw coordinator`, not plain `opencode`. It writes the session's config (model
  and variant, permission rules, the profile's files as instructions, the guard plugin), starts a server for it, asks
  the model for one call the guard always denies, and opens the TUI only when that call was stopped. `bin/hw start`
  refuses an OpenCode session that was not opened this way.
  <!-- test: test_hw_coordinator.py::KitTests.test_the_config_dir_holds_model_variant_permissions_instructions_and_the_guard; test_hw_coordinator.py::CommandTests.test_the_tui_opens_on_the_proven_session_and_the_server_and_record_end_with_it; test_hw_coordinator.py::CommandTests.test_a_session_whose_guard_is_not_live_is_never_opened; test_hw_coordinator.py::StartTests.test_start_refuses_an_opencode_session_the_coordinator_command_did_not_open -->
- A worker gets its own server, whose config holds Herdwright's rules and the hat's deny plugin. Before the brief the
  worker is asked for one call that every hat denies; a worker whose guard does not stop it gets no brief and its
  session is marked `failed`.
  <!-- test: test_hw_assign.py::OpenCodeTests.test_runtime_opencode_starts_a_private_server_a_session_with_model_and_variant_and_the_tui; test_hw_assign.py::OpenCodeTests.test_the_canary_is_denied_before_the_tui_starts_and_the_brief_goes_through_the_server; test_hw_assign.py::OpenCodeTests.test_a_worker_whose_guard_is_not_live_gets_no_brief_and_is_marked_failed -->
- The server password is kept out of the worker's shell, and the server's two shell routes run the same check as a
  tool call. The coordinator's own session has the tool-call check only, because its server's shell is your own (`!`
  in the TUI).
  <!-- test: test_hw_deny.py::OpenCodePluginTests.test_a_shell_the_server_starts_for_any_caller_is_checked_and_never_holds_the_password -->
- OpenCode has no hook that runs when a turn ends, so the watcher is one long-lived process, started by `start` and,
  if it died, started again by `start` and by `spawn`. For each event that needs the coordinator it prompts the
  coordinator's session through its server and keeps the id of the admitted message as a receipt; when that call fails
  it types the prompt into the coordinator's herdr pane. An event left un-acked is sent again, three times at most. The
  watcher ends when the coordinator loses the profile lock or its server stops.
  <!-- test: test_e2e_wake.py::Wake.test_an_idle_coordinator_is_woken_through_its_server_and_the_receipt_is_kept; test_e2e_wake.py::Wake.test_when_the_server_refuses_the_wake_is_typed_into_the_pane; test_e2e_wake.py::Wake.test_an_event_left_unacked_wakes_again_three_times_at_most; test_e2e_wake.py::Wake.test_one_watcher_per_profile; test_e2e_wake.py::Wake.test_the_watcher_stops_when_its_coordinator_loses_the_lock; test_e2e_wake.py::Wake.test_the_watcher_ends_with_the_coordinators_server; test_e2e_wake.py::Hold.test_a_watcher_that_died_is_started_again_by_start_and_spawn -->
- Not covered: a watcher that is killed while the coordinator is idle stays down until the next `start` or `spawn`; a
  wake that arrives while a turn is running is admitted to the session's inbox, and how OpenCode then orders it was
  not tested. An OpenCode worker has no turn-end guard: the watcher reports a worker that says done with an
  incomplete plan.
  <!-- note: what is not covered -->
  <!-- test: test_hw_assign.py::WatcherAndStartTests.test_a_done_with_an_incomplete_plan_is_reported_and_a_raise_pr_step_is_not -->
- An OpenCode worker reads the repository's `AGENTS.md` files and skills (`.opencode/skills`, `.claude/skills`); it
  does not read `CLAUDE.md`. This, and everything above against the installed `opencode`, is checked by
  `tests/smoke_opencode.py`, which is not part of the default test run because it needs a live herdr and starts model
  turns: `cd tests && SMOKE_OPENCODE_MODEL=provider/model SMOKE_OPENCODE_VARIANT=<variant> python3 -m unittest -v smoke_opencode`. Three of its checks
  need the model to do what it is asked, so they can fail without a fault in Herdwright.
  <!-- smoke: smoke_opencode.py::OpenCodeWorkerSmoke.test_2_a_guarded_worker_runs_the_model_is_denied_and_reads_agents_md; smoke_opencode.py::OpenCodeCoordinatorSmoke.test_4_a_guarded_coordinator_starts_holds_the_lock_and_cannot_change_a_project_repo; smoke_opencode.py::OpenCodeCoordinatorSmoke.test_6_an_idle_coordinator_is_woken_through_its_server_with_a_receipt_by_the_one_watcher -->

## How it is organised

Everything outside `profile/` is **core**: the same for everyone. Everything about you lives in **`profile/`**, which
exists only on your profile branch. The hooks refuse a commit on `main` that touches `profile/`.
<!-- test: test_hooks.py::PreCommit.test_main_takes_core_but_no_profile; test_hooks.py::PreCommit.test_a_profile_branch_takes_profile_and_core -->

| Path | What |
|---|---|
| `AGENTS.md` | The coordinator's manual, one for both runtimes (OpenCode reads it; Claude Code reads `CLAUDE.md`, which imports it) |
| `playbook.md`, `failure-modes.md` | Lessons about tools and workflow, loaded with the manual |
| `.claude/skills/` | The coordinator's workflows: onboarding, new-task, check-workers, relay, wrap-up, learn |
| `templates/` | The worker rules and the brief skeleton |
| `bin/hw` | The task database and herdr session driver (`bin/hw help`) |
| `bin/hw-setup` | Prerequisite check, git guard hooks (`hooks/`), runtime |
| `bin/hw-employee`, `bin/hw-deny` | Hats for workers (build, review, qa): `hw spawn hat=`, `hw-employee finish`, each hat's deny policy |
| `templates/hats/`, `templates/skills/` | Each hat's charter, deny policy and skills. A worker's first prompt names its hat's skills on either runtime, and `hw-employee skill show <name>` prints one |
| `bin/hw-review` | The mechanical facts of a review and the check on its findings draft: `check-draft` refuses a review blocker that names no ground (`requirement`, `bug` or `rule <n>`) |
| `bin/hw-core-sync` | Carries core improvements from your profile branch to `main` |
| `profile.template/` | The skeleton onboarding copies to `profile/` |

<!-- test: test_readme_claims.py::ReadmeClaims.test_every_path_the_table_names_exists; test_hw_repo_instructions.py::ToolInsideItselfTests.test_one_manual_serves_both_runtimes; test_hw_worker_skills.py::FirstPromptTests.test_claude_code; test_hw_worker_skills.py::FirstPromptTests.test_opencode; test_hw_review.py::DraftTests.test_a_blocker_names_an_objective_ground; test_hw_gates.py::CoreSyncCheckTests.test_check_lists_unsynced_core_commits_and_exits_1 -->

Your profile holds `profile.md`, `rules.md` (your rules for workers), `lessons.md`, `knowledge/`, `config/`, `briefs/`,
`db/` and `STATUS.md`. `bin/hw` reads `profile/` by default; set `HW_PROFILE_DIR` to point it at another profile, for
example a throwaway one for testing.
<!-- test: test_hw_setup.py::SetupCase.test_a_profile_kept_elsewhere_is_found_through_its_variable -->

In `config/settings.json`: `runtime` (`claude` or `opencode`), `guard` (protected branches, commit format) and
`worker.deny_tools`, the tool names workers may not call. Replace the template's list with your own connectors' write
tools.
<!-- test: test_hw_coordinator.py::RuntimeTests.test_one_key_names_the_runtime_and_another_value_is_refused; test_hw_guard.py::HookTests.test_profile_settings_drive_branches_and_format; test_hw_assign.py::RenderedPolicyTests.test_claude_settings_deny_the_tools_the_profile_names -->

## Tests

`cd tests && python3 -B -m unittest discover -v` runs everything except the OpenCode smoke run. The tests use fakes of
herdr, the runtimes and `gh` (`tests/fakes/`) and a throwaway profile: they need no account and start no model.
<!-- test: test_e2e_fakes.py::Herdr.test_a_started_agent_is_listed_with_its_pane_session_and_launch_flags; test_e2e_fakes.py::Gh.test_only_a_listed_pull_request_can_be_read -->

## Branches

- `main`: core only. The guard hooks refuse commits on `main` that touch `profile/` and pushes of a `main` that
  contains it.
  <!-- test: test_hooks.py::PreCommit.test_main_takes_core_but_no_profile; test_hooks.py::PrePush.test_a_main_that_holds_a_profile_is_not_pushed -->
- `profile/<name>`: core and your profile. Herdwright runs here. Push it only to a remote you control and that may hold
  your work data (usually a private one).
  <!-- test: test_hooks.py::PreCommit.test_a_profile_branch_takes_profile_and_core -->
- Improving Herdwright: a change that helps any user is committed as a core-only commit, and `bin/hw-core-sync`
  cherry-picks it onto `main`. To get others' improvements, update `main` (`git fetch && git branch -f main
  origin/main`, or pull in a separate worktree), then `git merge main` on your profile branch.
  <!-- test: test_hw_gates.py::CoreSyncCheckTests.test_check_lists_unsynced_core_commits_and_exits_1; test_hw_gates.py::MergeHookTests.test_merging_main_into_the_profile_branch_is_not_gated -->

## Upgrading

`git merge main` on your profile branch works with an older profile: columns and arguments of removed features are
migrated or refused, never silently used. `profile/employees/`, the `employees`, `queue`, `inbox`, `threads` and
`messages` tables and the `runtimes.json` config are unused now; delete them when you like.
<!-- test: test_hw.py::HwTests.test_schema_migrates_old_sessions; test_hw_assign.py::SpawnTests.test_the_arguments_of_removed_features_are_refused_not_ignored; test_hw_employee.py::DbTests.test_a_database_from_before_hats_lost_their_identities_keeps_working -->

- **Models (November 2026).** The template no longer ships a model, an effort, a variant or `effort_by_kind`. What
  your `config/claude.json` already sets keeps working, `effort_by_kind` included when you have it. An OpenCode profile
  that relied on the shipped model has none now: `bin/hw coordinator` asks for it once. `max_running` now also limits
  OpenCode sessions when `config/opencode.json` sets it.
  <!-- test: test_hw.py::HwTests.test_model_and_effort_come_from_claude_json_capped_by_its_ceiling; test_hw_coordinator.py::ModelQuestionTests.test_the_answer_opens_the_coordinator_on_that_model; test_hw.py::HwTests.test_max_running_counts_open_sessions_of_the_runtime_and_queues_the_rest -->
- **The rename from Zed.** Herdwright was called Zed until November 2026: the command was `bin/zed` with helpers
  `bin/zed-*`, workers reported with `ZED:` markers, and settings were `ZED_*` environment variables. All of these are
  now `bin/hw`, `bin/hw-*`, `HW:` and `HW_*`. Before you upgrade, let your worker sessions finish and close them, and
  close the coordinator session and any watcher it left running: a session started by the old code keeps the old names
  and cannot report to the new one.
  <!-- steps -->
- The first `bin/hw start` (on OpenCode, `bin/hw coordinator`) renames what your profile still calls by the old name,
  once, and lists each rename: `db/zed.db` to `db/hw.db`, `.cache/zed.lock` to `.cache/hw.lock`, and the `zed-deny` and
  `zed-pretool` plugin folders of OpenCode config folders under `.cache`. An old name next to its new one is left alone
  and reported. Until `start` has run, a command that needs the database refuses rather than open an empty `db/hw.db`.
  <!-- test: test_hw_rename.py::OldProfile.test_start_renames_the_old_names_once_and_says_so; test_hw_rename.py::OldProfile.test_an_old_name_next_to_its_new_one_is_left_alone_and_start_says_which_database_holds_the_tasks; test_hw_rename.py::OldProfile.test_before_the_first_start_a_database_command_refuses_and_creates_no_empty_database; test_hw_rename.py::ProfileMadeByTheOldCode.test_what_the_old_code_recorded_is_there_after_the_first_start_of_the_new_code -->
- While a live session of the old code still holds `.cache/zed.lock`, `start`, `coordinator` and `lock acquire` rename
  and take nothing, say so and stop: close that session and run it again. An old lock file that is empty or damaged
  counts as no lock.
  <!-- test: test_hw_rename.py::OldProfile.test_while_a_live_session_of_the_old_code_holds_the_old_lock_start_renames_nothing_and_stops; test_hw_coordinator.py::CommandTests.test_a_live_coordinator_of_the_code_before_the_rename_stops_it_before_anything_is_renamed_or_started; test_hw_rename.py::OldProfile.test_lock_acquire_is_stopped_while_a_live_session_of_the_old_code_holds_the_old_lock; test_hw_rename.py::OldProfile.test_an_old_lock_file_that_is_empty_or_not_json_does_not_stop_start -->
- When your profile is the checkout's own `profile/` folder, `start` also renames the sync point of the branch you are
  on, `refs/zed/core-sync/<branch>`, to `refs/hw/core-sync/<branch>`. With the profile elsewhere (`HW_PROFILE_DIR`) no
  git ref is touched: `start` names the old ref and prints the command that records it under the new name,
  `bin/hw-core-sync --mark <commit>`, where `<commit>` is what `git rev-parse refs/zed/core-sync/<branch>` prints.
  <!-- test: test_hw_rename.py::OldNamesInACheckout.test_on_its_own_profile_start_renames_the_sync_point_of_its_branch_once_and_no_other; test_hw_rename.py::OldNamesInACheckout.test_with_the_profile_elsewhere_start_touches_no_ref_of_the_checkout_and_says_how_to_keep_the_sync_point -->
- The old `ZED_*` variables are not read: set `HW_PROFILE_DIR` where you set `ZED_PROFILE_DIR`. `start` names any that
  are still set. When only `ZED_PROFILE_DIR` is set and no profile is found, `start` says so and tells the assistant not
  to onboard you again.
  <!-- test: test_hw_rename.py::OldNamesInACheckout.test_an_old_variable_is_not_read_and_start_names_it; test_hw_rename.py::OldNamesInACheckout.test_with_only_the_old_variable_set_start_says_one_thing_and_does_not_send_the_assistant_to_onboard -->
- Git guard hooks installed in worktrees by the old code (`zed-hooks`) are not migrated: `bin/hw-guard audit` lists
  those worktrees.
  <!-- test: test_hw_guard.py::HookTests.test_audit_worktree_findings -->
- **Runtimes.** If you cloned `main` while it supported Pi or Copilot: both are gone, and so is runtime selection
  (order, fallback, handoff, `runtimes.json`). Workers run on the install's runtime.
  <!-- test: test_hw_assign.py::OpenCodeTests.test_claude_stays_the_default_and_other_runtimes_are_refused -->
- **Hats, not names.** Workers wear a hat (`spawn hat=build|review|qa`, default build); `spawn employee=` is refused.
  Whether a reviewer or QA authored the code is checked when the session finishes, not when it is assigned.
  <!-- test: test_hw_assign.py::SpawnTests.test_hat_defaults_to_build_and_a_review_takes_hat_only; test_hw_assign.py::SpawnTests.test_the_arguments_of_removed_features_are_refused_not_ignored; test_hw_finish.py::FinishTests.test_a_review_that_changed_the_code_is_its_author_and_gives_no_verdict -->
- A hat session is known by the herdr pane that hosts its process (found through the process tree, not from
  `$HERDR_PANE_ID`, `$HW_SESSION` or `$HW_HAT`, which are only claims and are refused when they disagree), so it can
  finish only itself (`hw-employee finish`). The coordinator finishes a stuck worker with `--as-hw --session S<n>`,
  accepted only from a process in the pane of the session that holds the profile lock, with the lock's pid among its
  ancestors. `hw-deny` also refuses a hat session `--as-hw` and setting or unsetting the identity variables.
  <!-- test: test_hw_finish.py::FinishTests.test_the_identity_comes_from_the_process_not_from_the_variables; test_hw_finish.py::FinishTests.test_a_session_finishes_only_itself; test_hw_finish.py::FinishTests.test_a_hat_claim_that_disagrees_with_the_panes_session_is_refused; test_hw_finish.py::FinishTests.test_a_forged_hw_pane_without_the_locks_process_among_the_ancestors_is_refused; test_hw_deny.py::DecideTests.test_a_hat_session_may_not_set_or_unset_the_variables_that_say_which_session_it_is -->
- Out of scope: a worker that edits the herdr or database files directly, or runs its own script.
  <!-- note: a stated limit -->
- **Verdicts** given before the upgrade match no code (they attach to the tree outside `profile/`), so redo review
  and QA for work still open.
  <!-- test: test_hw_gates.py::MergeGateTests.test_refused_when_the_verdict_was_for_other_code; test_hw_gates.py::HwCheckoutMergeTests.test_a_profile_only_commit_after_clean_verdicts_keeps_the_merge_passing -->

## New machine

Clone, check out your profile branch from your remote, run `bin/hw-setup`, and start the coordinator in herdr. Worker
sessions and worktrees from the old machine will not exist; respawn from their pushed branches. Re-store any tokens
your profile names (see `profile/profile.md` and `playbook.md`).
<!-- steps -->

## Licence

Apache License 2.0 (`LICENSE`), Copyright 2026 Sumit Kumar Pandit (`NOTICE`). Six files are ported from firstmate
under the MIT licence: `THIRD_PARTY_LICENSES.md`.
<!-- test: test_hw_release.py::LicenceTests.test_licence_is_the_unmodified_apache_2_text; test_hw_release.py::LicenceTests.test_notice_names_the_project_and_firstmate; test_hw_release.py::LicenceTests.test_each_ported_file_attributes_firstmate -->
