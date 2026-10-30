# Changelog

All notable changes to agenticOS. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/) (0.x: anything may still change).

Releases before 0.7.0 were numbered after the fact, one per merged pull request, and are not tagged yet.

## [0.11.0] — unreleased (`feat/operation-types`)

### Added
- **Operation types** for board tasks: `feature`, `change`, `fix` or `refactor`, optional (an untagged task works as before). Set with `aos task add --op` or `task_create(operation=)` (planner and worker tools); an unknown value is refused with the allowed list.
- The worker's context gets a "How to do this <operation>" section with its first move: a `fix` reproduces the bug as a failing test first and names that test in its `task_complete` summary; a `refactor` keeps behaviour and existing tests unchanged; a `change` updates its tests to the new behaviour; a `feature` adds tests for the new behaviour.
- The reviewer's context gets what to check: a `fix` without a regression test, or a `refactor` that deletes, skips or weakens tests, is a `review_fail`; for `change` and `feature`, the tests must cover the new or changed behaviour.
- `aos task show` prints `operation:`, `aos board` tags the title (`[fix] …`) and `board_status` tasks include `operation`.
- The `aos-feature` skill gives each task an operation when drafting tasks.

## [0.10.0] — 2026-10-30 (PRs #12, #13, #14)

### Added
- **Worker-to-worker messages** within a feature: heads-up broadcasts (`task_broadcast`), direct questions (`task_message(..., question=true)`) and their answers (`task_reply`). They are recorded on the board and delivered live to running workers like steers; a worker that hasn't started finds them in its context.
- A question to a finished task gets an automatic reply with its result. `escalate=true` sends it to the planner (`board_wait` attention, `message_answer`).
- Limits: `board.messages` (`enabled`, `max_per_attempt: 5`, `max_chars: 1500`). Replies don't count, a reply only answers a question, once, and secrets are refused. Messages are quoted information, never instructions.
- `aos board` shows open questions; `aos task show <id> --messages` shows a task's thread; `board_status` has `messages` and `open_questions`.

### Docs
- README: a beginner section, 'New here? agenticOS for dummies' (PR #12).
- Worker-messages spec and plan (PR #13; the implementation follows in this release).

## [0.9.1] — 2026-10-30 (PR #11)

### Fixed
- After a dispatcher crash, a review that was in progress is retried instead of staying stuck, and stale Kiro agent files of reviewers and learners are removed.
- Denying or expiring a permission request resets the stall clock, like approving does, so a run isn't marked stalled right after a long wait.
- `aos board run --worker` refuses an unknown tool up front with a clean error.
- `aos board run --worker` no longer switches the tool of a task marked resume; the timeline says why.
- `aos board` and `board_status` show Kiro credits (`cost_credits`), not only USD.
- The ACP run log is closed when the agent process can't start.
- The tool/model/transport column of `aos board` grows to fit long model ids.
- A shell command whose title looks like `scripts/x.sh` is no longer treated as an MCP tool.
- Approving with `--always` a request that has no reusable rule says it was allowed once, instead of "(always)".
- A lesson rejected automatically by `lesson_contradict` is noted on the task's timeline.
- A declined promotion is not proposed again when a third project links into its group.
- Promotions are staged under a lock, so the dispatcher and a CLI confirm can't stage duplicates.
- An applied promotion marks only the group's confirmed lessons as promoted.
- Lesson project names are checked (no `..` or paths); only valid slugs count as lesson projects.
- A lesson file whose name isn't a lesson id is skipped with a warning, like a broken file.
- `aos doctor` checks that the models in `aos.yaml` (default, learning model, tiers) fit their tool.
- `aos context` error output is always one line (YAML parse errors spanned several).
- `aos sync` only writes the manifest when it changed.

## [0.9.0] — 2026-10-30 (PR #10)

### Added
- **Trivial tasks skip the independent reviewer** once their checks pass (`board.skip_review_sizes`, default `[trivial]`).
- **Security-focused review.** A change marked by the planner (`aos task add --security`, `task_create(security=true)`), or detected by fixed rules from its file paths and added lines, gets a reviewer with a security checklist. Any real security finding fails the review. The timeline records why.
- **Optional second independent reviewer** for risky tasks: `board.second_review: [large, security]`. Both passes are needed, each reviewer starts fresh, and the retry rule for flaky reviewers restarts for each round.
- The `aos-feature` planner marks security-sensitive tasks and only sizes genuinely mechanical changes as `trivial`.

## [0.8.0] — 2026-10-30 (PR #9)

### Added
- **Model per task, set by hand:**
  - `aos task add --model`, `aos feature new --model`, a project's `worker: {model: …}`, `aos task retry --model` (`auto` goes back to auto);
  - `aos board run --model` applies to that run only and is never stored;
  - MCP `task_create(model=)` / `feature_create(model=)` / `task_retry(model=)`;
  - precedence: task > feature > project.
- **Auto mode:** a task's **size** (`trivial | small | standard | large`) picks the tool's model tier when nobody chose a model by hand. Claude's tiers are haiku / sonnet / sonnet / opus; Kiro's own auto mode decides by default. The `aos-feature` planner sizes every task, and you can pass `aos task add --size`.
- A model that doesn't fit the task's tool is refused when given directly. Claude ids use hyphens (`claude-opus-4-1`); Kiro's own names are dotted (`claude-sonnet-4.5`). When `--worker` switches a task's tool (`board run` or `task retry`), a hand-chosen model that doesn't fit the new tool goes back to auto, with a timeline note.
- `aos board` shows `tool/model/transport`; `aos task show` and `board_status` say where the model came from.

## [0.7.0] — 2026-10-29 (PR #8)

### Added
- **Learning loop from the board.** When a board task finishes after a correction, a short read-only *learner* run turns it into a lesson.
  - **Corrections:** a failed review or check, a retry, your steer, a denied permission, or a block.
  - **The learner run:** cheap model (`workers.<tool>.learning_model`, `haiku` on Claude), lesson tools only, `aos serve --learn`.
  - **The lesson:** one trigger and one action, with evidence and a confidence score (0.3–0.9) that follows fixed rules.
  - **Where lessons go:** `~/.agenticos/data/lessons/<project>/` (local only). The best ones reach that project's worker contexts and your sessions, ranked and capped. If space runs out, lessons are cut before memory.
  - **Your control:** `aos lessons`, `aos lessons confirm|reject <project>/<id>`, `aos lessons learn <task>`, MCP `lessons_list` / `lesson_decide`. The `aos-feature` skill reports "Lessons learned this feature".
  - **Promotion across projects:** a lesson confirmed in two or more projects (linked by the learner with `same_as`) is proposed as one org memory entry in the inbox (`aos inbox apply|reject`).
  - **Settings:** `learning.enabled`, `max_in_context`, `min_confidence`, `learner_timeout_min`, `max_ops_per_run`.
  - **Board and doctor:** `board_status` gains `lessons` and `pending_promotions`, and `aos doctor` reports lesson counts and broken lesson files.
- Worker contexts name the project's `verify_command`, so workers run it before `task_complete`.
- **GitHub Actions CI:** the test suite runs on every pull request to `main` and on pushes to `main` (Ubuntu with Python 3.11 and 3.13, plus macOS).

### Fixed
- **A string `verify_command` ran only its first command.** It was split into arguments instead of being run by a shell, so in `npm ci && npm test` only `npm ci` ran, and a failing check could pass. It now runs through `/bin/sh -c`.

## [0.6.1] — 2026-10-09 (PR #7)

### Security
- **Workers never skip the project's git hooks.** `--no-verify`, `git commit -n` and anything that sets `core.hooksPath` are refused. For Kiro, the same patterns are also in the per-run agent's `deniedCommands`.
- **Editing an existing lint, format or git-hook config asks you first.** This covers `.eslintrc*`, `prettier`, `ruff.toml`, `.pre-commit-config.yaml`, `.husky/` and similar files. Creating one is fine.

## [0.6.0] — 2026-10-08 (PR #6)

### Added
- **Live worker sessions over ACP** (Agent Client Protocol) for Claude Code (adapter: `aos acp install claude`) and Kiro (`kiro-cli acp`). If ACP is unavailable, workers fall back to the CLI. With a live session:
  - **Permission requests:** a request outside the allow-list waits for your decision (`aos task approve [--always]`, `aos task deny`, `permission_decide`) instead of failing the attempt. Dangerous requests are rejected outright.
  - **Stall detection** and **one nudge** when a turn ends without a verdict.
  - **Mid-turn steering:** Claude gets steers inside its current turn; Kiro queues them, and `--now` interrupts the turn.
  - **Resume** reloads the previous session on a retry.
  - **Cost from the stream:** USD for Claude, credits for Kiro, with budgets per unit.
- **A global per-machine tool setting:** `aos worker claude|kiro`. It applies after explicit choices: task > feature > project > global > team default. `--worker` on `init`, `feature new` and `board run`.
- A plain chain of allowed commands is allowed, and an approved request isn't asked again in the same attempt.
- The project's `verify_command` is allowed to workers without asking.
- Run logs carry the ACP traffic.
- `aos doctor` reports Node, the adapter and the transport per tool.

### Fixed
- Final-review fixes:
  - a newline in a command bypassed the chaining check;
  - `~` and `$VAR` paths counted as inside the worktree;
  - stale or hanging permission requests;
  - "always" handed decisions to the agent's own rules;
  - an untracked Claude turn after a steer sent while no turn was running;
  - the configured model was ignored over ACP;
  - cost was counted twice or lost;
  - real turn errors were reported as "no verdict".
- A worker's steer was replayed to its reviewer.

## [0.5.0] — 2026-10-07 (PR #5)

### Added
- **Board hardening:**
  - claims are verified (new commits plus the project's `verify_command`) before an independent reviewer checks the task's diff;
  - a rejected attempt is reverted before the retry;
  - the same failure twice stops the task;
  - attempt tokens refuse stale workers;
  - a resume hint after an interrupted attempt;
  - parallelism is sized by free memory, and starts are staggered;
  - cost per attempt, with task and feature budgets;
  - `aos task steer`.
- `ROADMAP.md`.

## [0.4.0] — 2026-10-06 (PR #4)

### Added
- **The `aos-feature` master skill:** plan, create, run, follow and report a multi-project feature from one conversation. It uses `board_wait`, a zero-token wait for anything that needs attention, and replaces `aos-plan-feature`.
- **Kiro workflow front-end:** `aos feature workflow` writes a Kiro Workflows recipe. It runs the board with one live watch per task and pauses for blocks, failures and contract changes.

## [0.3.0] — 2026-10-02 (PRs #2, #3)

### Added
- The planner starts and follows parallel workers itself (`board_start`, `board_status`).
- `code_query`: query any linked project's graphskill code graph from any session.

## [0.2.0] — 2026-10-01 (PR #1)

### Added
- **The multi-project board:**
  - features with a shared contract, tasks per project with dependencies, and contract proposals;
  - a SQLite file at `~/.agenticos/data/board.db`;
  - a dispatcher with parallel Claude Code and Kiro workers in isolated git worktrees, with retries, timeouts and recovery;
  - board CLI and MCP tools for workers and planners.

## [0.1.0] — 2026-10-01

### Added
- **The agenticOS core:**
  - capped `org` / `project` / `user` memory, with org changes through the inbox;
  - skills, written by agents and reviewed in git;
  - full-text knowledge search;
  - session context injected at start;
  - `aos link` / `sync` / `unlink` for Claude Code and Kiro;
  - the `aos` MCP server and CLI.
- Business data is kept local-only in `~/.agenticos/data`, never in the repo.
