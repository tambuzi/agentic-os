# Spike findings: can Kiro workflows drive agenticOS?

Run: 2026-10-06, **Kiro IDE** (workflow engine present), macOS, `aos` editable
install pointing at this repo. The `aos-spike` workflow was executed end-to-end
from the IDE Workflows engine (workflow id `wf_4880298722d6c45d`, status
`completed`).

> An earlier version of this file recorded a CLI-only run (`kiro-cli 2.23.0`) that
> had **no** workflow engine, so every check was answered by exercising the
> underlying mechanism by hand. That is now superseded: the four checks below are
> answered from a real IDE workflow run.

## How it was run

The workflow file in `.kiro/workflows/` had to be adjusted before the engine would
launch it. Three hard launch constraints surfaced:

1. **Filename suffix.** The runner only accepts `*.workflow.yaml` / `*.workflow.yml`
   / `*.workflow.json`. `aos-spike.yaml` was rejected until copied to
   `aos-spike.workflow.yaml`.
2. **Custom step agents are rejected at validation.** The `custom-calls-aos` step
   used the workspace agent `aos-spike-agent`; the validator rejects any non-bundled
   step agent (only `wf-*` and `semantic_reviewer` are allowed) and **fails the whole
   workflow before any step runs** — you cannot let just that one step fail. The step
   was removed into a trimmed copy to run the rest.
3. **`command` watch `pollIntervalSec` minimum is 10s.** The spike's `5` was rejected.

With those three fixes the run completed: `builtin-calls-aos → watches (watch-a ‖
watch-b) → report`, all nodes `completed`.

## The four questions

### 1. `watch-a` / `watch-b` — did both run `aos board watch` and end `terminal-state`, or error?

**Both ran `aos board watch` successfully, no error.** Each watch's captured output
was `{"polls": 1}` — the `command` handler spawned the `aos` CLI (on PATH, no
`command not found`), got valid JSON back, and the parallel `allSettled` join
completed cleanly. The watches terminated at `polls: 1` here (the IDE's scheduler
settled the node after a single successful poll in this run) rather than iterating to
the demo's internal 3/6 terminal count; the `--demo N` ceiling is the CLI's own
counter, not what gated the node. Headline: the CLI watch contract works end-to-end
inside the engine.

### 2a. `builtin-calls-aos` — slugs, or "NO AOS TOOLS"?

**It listed the project slugs** (not "NO AOS TOOLS"). The bundled `wf-planner` step
agent reached the `aos` MCP server configured in `.kiro/settings/mcp.json` and
`project_list` returned the 6 linked project slugs (5 work projects, names
withheld because business data stays local, plus `spike`).
**Confirmed:** a built-in workflow step agent inherits `.kiro/settings/mcp.json` and
can call aos tools.

### 2b. `custom-calls-aos` — did `aos-spike-agent` run, or did Kiro reject it?

**Rejected.** Kiro will not run a workspace custom agent as a workflow step agent.
Validation fails with: *"Workflow references custom agent 'aos-spike-agent' which is
not registered. Registered step agents: semantic_reviewer, wf-auto-researcher,
wf-coder, wf-design, wf-design-reviewer, wf-planner, wf-pr-responder,
wf-pr-submitter, wf-review-aggregator, wf-workflow-creator."* The rejection is
whole-workflow, at validation time — the step never executes. (The agent is a valid
*interactive* agent and does reach the aos tools when run on its own; it just cannot
be a workflow step agent.)

### 3. Did the two watches run side by side at zero model turns?

**Yes.** `watch-a` and `watch-b` are children of a `parallel` node and ran
concurrently. `command` watches are pure CLI handlers with no LLM involvement, so
each poll is zero model turns by construction — the watches wait and poll without
consuming any model turns.

### 4. report line 4 — `worktree_path` / `run_id` real values or literal `{{…}}`?

**Literal — "not set".** The report step rendered them verbatim as
`{{worktree_path}}` and `{{run_id}}`. These are not auto-injected context variables;
they only resolve if declared as workflow `inputs` (or produced by an earlier step).
A workflow that needs a worktree path or run id must thread it through explicitly.

## Answer to the spike's question

- **Can a Kiro workflow be the front-end for the aos board?** Yes, in the Kiro IDE.
  The engine runs step / parallel / watch nodes, bundled step agents reach the aos
  MCP server, and `command` watches drive the `aos` CLI with zero model turns.
- **Can it start the board through aos tools?** The plumbing is confirmed reachable:
  a bundled step agent inherits `.kiro/settings/mcp.json` and successfully called
  `project_list`; the same path exposes `board_start`, `board_status`,
  `feature_create`, `task_create`, etc.
- **Zero-token `watch` node following a task?** Yes — `command` watches are
  model-free and ran side by side in parallel. The `aos board watch` contract
  (cursor in → one JSON out) works inside the engine.
- **The real constraints to design around:** (a) only bundled step agents are
  allowed — no custom workspace agent as a step, so an aos-specific step agent is
  out; reach aos via the MCP server from a bundled agent instead; (b) watch
  `pollIntervalSec` floor is 10s; (c) `{{worktree_path}}`/`{{run_id}}` are not free —
  they must be declared inputs; (d) workflow files must be `*.workflow.{yaml,yml,json}`.

## Recommendation

Running `aos-spike` from the IDE answers the open questions positively enough to
proceed. If we build `aos feature workflow <slug>`, generate recipes that:
- use **bundled** step agents (e.g. `wf-coder`/`wf-planner`) that pick up the aos MCP
  server from `.kiro/settings/mcp.json` — do not emit custom step agents;
- declare `run_id` / `worktree_path` (and any board/task/feature ids) as explicit
  workflow `inputs`;
- keep watch `pollIntervalSec >= 10`;
- name files `<slug>.workflow.yaml`.

The one piece still worth a live pass is a **real board task** watch (`--task <id>`
instead of `--demo N`) to confirm the terminal payload relays `result` and the last
`blocker` through the engine.

## Reproduce

```bash
# 1. install the correctly-suffixed recipe (bundled step agents only), e.g.:
#    .kiro/workflows/aos-spike.workflow.yaml
# 2. ensure .kiro/settings/mcp.json defines the `aos` server
# 3. in the Kiro IDE Workflows panel (or chat): "run the aos-spike workflow"

# sanity check the watch contract (product code) on its own:
echo '{"cursor": null}' | aos board watch --demo 1   # one JSON object, terminal-state
```
