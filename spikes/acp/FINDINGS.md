# Spike (throwaway): ACP sessions instead of one-shot CLIs

**Question.** Can aos drive its board workers through live **ACP** (Agent Client Protocol) sessions instead of one-shot `claude -p` / `kiro-cli chat --no-interactive` runs? We want streamed events, approvals routed to us, mid-turn steering, cancel and resume (ROADMAP item 2).

**How.** `probe.py` is a ~150-line Python ACP client: newline-delimited JSON-RPC 2.0 over stdio. It was run on 2026-10-07 against the real Claude adapter that Kiro Crew uses, in a scratch git repo, with the `aos` MCP server attached to the session.

## Claude: `@agentclientprotocol/claude-agent-acp` 0.87.0 (Claude Code 2.1.286, Node 26)

Launch: `node <pkg>/dist/index.js` with `CLAUDE_CODE_EXECUTABLE=$(which claude)` set. The adapter does not search PATH.

| Check | Result |
|---|---|
| `initialize` | protocol v1; advertises `_meta.steering.supported: true` |
| `session/new` with `mcpServers` | ✅ it got the `aos` server and called `memory_read` through it |
| Session modes | `default` (asks), `acceptEdits`, `plan`, `auto`, `bypassPermissions`; switchable per session |
| **Streamed events** | ✅ 34 `session/update`s in a 17 s turn: `tool_call`, `tool_call_update`, `agent_message_chunk`, `usage_update`, `available_commands_update` |
| **Permission requests to the client** | ✅ `session/request_permission` twice (write, MCP tool); answering `allow_once` let the work proceed (`hello.txt` = `hi`) |
| **Cancel mid-turn** | ✅ `session/cancel` → `stopReason: cancelled` immediately |
| **Mid-turn steering** | ✅ `_session/steering` (not Crew's `_session/steer`, which returns method-not-found) with `_meta.steering.priority: "now"` → `outcome: injected`. The 30 s loop stopped and the same turn ended with `STEERED` after 14.8 s |
| **Resume** | ✅ `session/load` with the old `sessionId` in a **new** adapter process: it remembered what it had done |
| Memory | ~400 MB peak for adapter + Claude Code + MCP servers during a turn |
| Usage/cost | `usage_update` events stream during the turn (exact cost field still to map) |

## Kiro: `kiro-cli acp` 2.23.0 (macOS, run 2026-10-07)

Launch: `kiro-cli acp`. Kiro serves ACP itself, so no adapter or Node is needed. Run: `python3 spikes/acp/probe.py kiro kiro-cli <git-workdir> "$(which aos)"`.

| Check | Result |
|---|---|
| `initialize` | protocol v1; `loadSession: true`, MCP over stdio and http (no sse); no steering advertised |
| `session/new` with `mcpServers` | ✅ it got the `aos` server and called `memory_read` through it. It *also* loads the user's global Kiro MCP config (a broken `sonarqube` server showed up as `_kiro.dev/mcp/server_init_failure`) |
| Session modes | Kiro agents rather than permission modes: `kiro_default`, `kiro_planner`, `kirocrew-worker`, etc. Models: `auto`, `claude-opus-5.5`, `claude-sonnet-5.5`, … |
| **Streamed events** | ✅ `tool_call`, `tool_call_update` (one per output line of a shell command), `agent_message_chunk`, plus `_kiro.dev/*` extension notifications (`metadata`, `session/update` with `tool_call_chunk`, `commands/available`, `subagent/list_update`) |
| **Permission requests to the client** | ✅ `session/request_permission` twice (write, MCP tool); `allow_once` let the work proceed (`hello.txt` = `hi`) |
| **Cancel mid-turn** | ✅ `session/cancel` → `stopReason: cancelled` immediately. ⚠️ **Race:** the turn is still shutting down when that reply arrives. A `session/prompt` sent at once comes back `refusal` in 0 s, and its real answer arrives under the *next* prompt (replies are off by one turn). The turn has really ended once a trailing `_kiro.dev/metadata` with `meteringUsage` arrives. Waiting ~3 s also fixes it |
| **Mid-turn steering** | ⚠️ **Queued, not injected.** `_session/steering` → method-not-found. `_session/steer` needs `{"sessionId", "message": "<string>"}` (sending Claude's `prompt` blocks gives a parse error) and replies `{"queued": true}`. The running 30 s command was **not** interrupted. After it finished, the same turn followed the steer (it skipped the summary and acknowledged STEERED): 34.7 s in total, vs 14.8 s on Claude |
| **Resume** | ✅ `session/load` in a **new** `kiro-cli acp` process: it remembered creating `hello.txt` |
| Memory | ~90–300 MB peak for the process group during a turn (two runs) |
| Usage/cost | ✅ per turn in `_kiro.dev/metadata` → `meteringUsage: [{value, unit: "credit"}]` (~0.05 credits for a trivial turn); also `contextUsagePercentage` |

## Answer

**Yes for both. Claude has everything we wanted; Kiro has everything except true mid-turn steering.** Live sessions give us four things one-shot CLIs can't:
1. **Liveness:** stall detection from stream silence instead of a 45-min wall clock.
2. **Approvals routed to aos:** a tool outside the allow-list can be asked about (to the planner or user) instead of the worker failing.
3. **Real steering:** the user's message changes the current turn.
4. **Clean cancel and resume:** without killing processes and losing the session.

**Costs to design around:**
- **~400 MB per live Claude session, ~100–300 MB for Kiro.** This fits the memory-gated starts we already have; `worker_memory_gb` should become 0.5–1.0 when ACP is on.
- **A Node dependency** for Claude (the adapter is an npm package).
- **Steering differs per tool, in name, payload and meaning.** Claude: `_session/steering` with `prompt` blocks + `_meta.steering.priority`, injected mid-turn. Kiro: `_session/steer` with a `message` string, queued until the running tool finishes. A steer can't interrupt a long Kiro tool call, so `aos task steer` on Kiro may also need `session/cancel` + a new prompt.
- **Kiro's cancel reply arrives before the turn has ended.** The runner must wait for the turn-end `_kiro.dev/metadata` (or retry once on an instant `refusal`) before sending the next prompt.
- **Cost is reported differently.** Claude: `usage_update`. Kiro: `_kiro.dev/metadata.meteringUsage` in credits.
- **Kiro sessions inherit the user's global MCP servers** on top of the ones we pass. Workers may need a dedicated Kiro agent/profile to stay hermetic.
- **Approval option kinds differ per tool**, so they must be mapped.

## Recommendation

Build an ACP runner as a new worker *transport* behind the existing adapters, keeping one-shot CLIs as a fallback:
- one long-lived session per worker or reviewer;
- the dispatcher consumes the event stream (liveness, progress, cost);
- answers permission requests from the neutral allow-list, and **escalates the rest** to the board as attention items;
- delivers `aos task steer` as `_session/steering`;
- cancels cleanly.

Claude first; Kiro is verified too, with the queued-steer and cancel-race caveats above handled in its transport.
