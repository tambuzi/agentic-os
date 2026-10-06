# Kiro workflow front-end for the aos board

Date: 2026-10-06. Builds on the board spec and on `spikes/kiro-workflows/FINDINGS.md`.

## Intent
Kiro users drive a planned feature from Kiro's Workflows panel. They see each task's live state, and answer blockers, contract proposals and failures inside Kiro. The aos board still does the execution: worktrees per project, Claude or Kiro workers, retries. Claude Code users are unaffected.

## Constraints from the spike
- Step agents must be bundled (`wf-*`, `semantic_reviewer`). A custom agent rejects the whole workflow at validation. Bundled agents reach `aos` through `.kiro/settings/mcp.json`.
- Files must be named `*.workflow.yaml`. Watch `pollIntervalSec` must be at least 10. At most 50 static nodes, nesting depth 8.
- A watch node completes on its **first `new-activity`**, not only on `terminal-state`.
- `{{run_id}}` / `{{worktree_path}}` are not injected by Kiro. The generator embeds ids literally instead.

## Design
`aos feature workflow <slug> [--out DIR] [--poll SEC]` and the planner MCP tool `feature_workflow(feature)` write `<project>/.kiro/workflows/aos-<slug>.workflow.yaml`:

```
start (wf-planner: board_start)
parallel tasks (allSettled)
  repeat task-N (stopWhen watch-tN.terminal, maxIterations 20, onMaxIterations pause)
    watch-tN  (command: <abs aos> board watch --task N, pollIntervalSec >= 10)
    handle-tN (wf-planner: relay to the user with send_message warning, act, board_start again)
summary (wf-planner: board_status, results and worktree paths)
```

`aos board watch --task N` returns:
- **`idle`** while the task is just running or waiting;
- **`new-activity`** when it needs a human: it is `blocked`, it has a pending contract proposal, or it is `failed`. Only when that "attention signature" changed since the cursor, so an unanswered blocker isn't re-asked on every poll;
- **`terminal-state`** when it is `done` or `cancelled`.

New planner tools let the handle step act on your answer: `task_unblock`, `task_retry`, `task_cancel`, `proposal_decide`.

The generator refuses recipes over 50 nodes (about 15 tasks) and points to `aos board run` instead. Follow-up tasks that workers create after generation are not in the recipe. The summary step uses `board_status`, which covers every task.

Not verified until a live Kiro run: the exact placement of `stopWhen` on a `repeat` node, taken from the authoring doc.
