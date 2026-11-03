# Skill compliance tests end to end (claude), 2026-11-03

`aos skills test aos-feature` with real Claude Code (`claude -p`, model from `workers.claude.model`),
run by the real install (`~/.agenticos/venv`).

| Run | Result | What it showed |
|---|---|---|
| 1 | setup failed | This repo's `.venv` had the iCloud hidden `.pth` problem, so `python -m aos` failed outside the repo. That's an environment issue: cleared with `chflags nohidden`, and the runs below use the real install. |
| 2 | 1/2 | `asks-before-creating` passed. `plan-two-projects` created no tasks. The report had no replies, so it couldn't show why. **Fix:** the report now includes each turn's final reply (≤ 1,500 chars). |
| 3 (`--keep`) | 0/1 | The replies showed two causes. (a) Every `mcp__aos__*` call was refused: `--permission-mode acceptEdits` doesn't allow MCP tools headless. **Fix:** the runner passes `--allowedTools mcp__aos`. (b) The demo repos are empty, so the agent asked what to build instead of planning. **Fix:** the scenario's first turn says the code is built from scratch, with ISO 4217 and USD by default. |
| 4 | **2/2 pass** | $0.52 and 103 s in total. |

Run 4 in detail:
- **plan-two-projects** ($0.37, 64 s). Calls: Skill → project_list → knowledge_search → code_query ×2 → project_context → feature_create → task_create ×2 (api, web) → board_start (dry run, "would start the dispatcher") → board_wait. No task_complete.
- **asks-before-creating** ($0.15, 39 s). It explored with project_context, code_query and Bash, presented the plan, and created nothing.
- **Clean-up:** no `~/aos-skilltest-*` left, and the demo transcripts were removed from `~/.claude/projects`.
- **Seen, not a failure:** after the dry `board_start`, `board_wait` reports `waiting_on_human` with nothing pending, and the agent says it can't tell why. This is expected in a dry run. The scenario ends there.
