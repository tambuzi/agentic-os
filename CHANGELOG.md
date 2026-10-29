# Changelog

All notable changes to agenticOS. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/) (0.x: anything may still change).

Releases before 0.7.0 were numbered after the fact, one per merged pull request, and are not tagged yet.

## [0.7.0] — unreleased (`feat/learning-loop`)

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
