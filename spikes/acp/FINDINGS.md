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

## Kiro: `kiro-cli acp`, to run on the Kiro machine

`kiro-cli` isn't installed on the dev Mac. From Kiro Crew's source: Kiro serves ACP itself via `kiro-cli acp`; approval option kinds are spelled differently (`allow_once`/`allow_always` vs Claude's); and Crew sends Kiro its `_session/steer` extension. Run:

```bash
cd ~/agenticOS && git checkout spike/acp
mkdir -p /tmp/acp-work && cd /tmp/acp-work && git init -q -b main && echo x > README.md && git add . && git commit -qm init
cd ~/agenticOS && python3 spikes/acp/probe.py kiro kiro-cli /tmp/acp-work "$(which aos)"
```

Send back the JSON it prints, especially:
- `3_prompt_with_permission`: permission requests and streamed update kinds;
- `5_mid_turn_steer`: which method worked;
- `6_resume`: whether `session/load` works.

## Answer

**Yes for Claude, with everything we wanted.** Live sessions give us four things one-shot CLIs can't:
1. **Liveness:** stall detection from stream silence instead of a 45-min wall clock.
2. **Approvals routed to aos:** a tool outside the allow-list can be asked about (to the planner or user) instead of the worker failing.
3. **Real steering:** the user's message changes the current turn.
4. **Clean cancel and resume:** without killing processes and losing the session.

**Costs to design around:**
- **~400 MB per live session.** This fits the memory-gated starts we already have; `worker_memory_gb` should become 0.5–1.0 when ACP is on.
- **A Node dependency** for Claude (the adapter is an npm package).
- **Steering method names differ per tool.** Use `_session/steering` (claude-agent-acp), and fall back to `_session/steer` (Crew's name for other harnesses).
- **Approval option kinds differ per tool**, so they must be mapped.

## Recommendation

Build an ACP runner as a new worker *transport* behind the existing adapters, keeping one-shot CLIs as a fallback:
- one long-lived session per worker or reviewer;
- the dispatcher consumes the event stream (liveness, progress, cost);
- answers permission requests from the neutral allow-list, and **escalates the rest** to the board as attention items;
- delivers `aos task steer` as `_session/steering`;
- cancels cleanly.

Claude first (verified here); Kiro once `probe.py kiro` confirms its side.
