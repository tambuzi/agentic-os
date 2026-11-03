# Skill health end-to-end (claude)

Run: `PATH=/opt/homebrew/bin:$PATH ~/.agenticos/venv/bin/python spikes/skill-health-e2e/health_run.py`.

An earlier run was blocked by this machine, not the code. `/usr/local/bin/node` (Homebrew Intel, 22.1.0)
can't load `libicui18n.74.dylib` since icu4c moved to 77, so the Claude ACP adapter died at start
("the agent exited (-32000)"), for the session learner too. With the working `/opt/homebrew/bin/node`
first on PATH it passes. That earlier run also showed two gaps, now fixed with tests:
- a failing improver run would have been retried on every session end (now at most 3 times per 14 days);
- a crash surfaced as an exception instead of a failed run with its reason.
The five sessions here include three resumed and corrected ones (`claude -p --resume`): they were
counted as corrected thanks to the resumed-session fix in this branch.

- date: 2026-11-03T09:02
- result: **PASS** (4/4)

## Checks

- ✅ aos link rendered the demo-naming skill
- ✅ five uses recorded, three corrected
- ✅ demo-naming is flagged
- ✅ a skill-health proposal in the inbox

## Driver

```
08:59:53 ok   aos link rendered the demo-naming skill
09:00:09 session 9a778e95-8cb6-421c-8e57-c703c1242f36 (loading data): ok
09:00:21 session 1e887fcd-765f-4369-a4c1-0f181e875734 (parsing dates): ok
09:00:40 session f9ae158b-53ac-4f78-840b-57dd14acad25 (sending emails): corrected
09:01:02 session 1a6a0cd8-78b2-4134-abc5-daf482ffc687 (caching results): corrected
09:01:24 session 2438943c-c3e0-49e1-a538-edc169f298c3 (reading config): corrected
09:01:24 uses: [["ok", "9a778e95"], ["ok", "1e887fcd"], ["corrected", "f9ae158b"], ["corrected", "1a6a0cd8"], ["corrected", "2438943c"]]
09:01:24 ok   five uses recorded, three corrected
09:01:25 aos skills health:
skill                              uses 7d/30d  corrected 7d/30d  trend  tests  last used
demo-naming                          5 / 5        60% / 60%       =      -      2026-11-03  flagged

09:01:25 ok   demo-naming is flagged
09:02:04 proposal: {"kind": "skill", "op": "patch", "reason": "skill health: 3 of 5 uses corrected in 30 days. Three user corrections all show project uses snake_case naming, not CamelCase. Agent created ConfigReader.py, ResultCache.py, and EmailSender.py in CamelCase, each time corrected to snake_case. Skill must teach the actual convention.", "args": {"name": "demo-naming", "old": "1. Name the module file in CamelCase, e.g. `DataLoader.py`.", "new": "1. Name the module file in snake_case, e.g. `data_loader.py`."}}
09:02:04 ok   a skill-health proposal in the inbox
```

## sessions.log (tail)

```
done: Module files must use snake_case naming (email_sender.py, not EmailSender.py). Caught this once during file creation; saves a correction on next module.
done: Confirmed: module files use snake_case (result_cache.py, not ResultCache.py). Lesson strength increased.
```

## improve-demo-naming.log (tail)

```
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"Bug"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" clear"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":":"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" skill"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" says CamelCase,"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" but all"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" three corrections show"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" project"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" uses"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" snake_case. Agent"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" failed"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" same"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" way"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" three"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" times."},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"\n\nFixing"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" naming"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" convention in"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" step"},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":" 1."},"messageId":"msg_011CgfeL3UeymE7AVCjNoFaY"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"_meta":{"claudeCode":{"toolName":"mcp__aos__propose_skill_patch"}},"toolCallId":"toolu_01PNddN6x7QwvBX7qA65XBj5","sessionUpdate":"tool_call","name":"mcp__aos__propose_skill_patch","rawInput":{},"status":"pending","title":"mcp__aos__propose_skill_patch","kind":"other","content":[]}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"_meta":{"claudeCode":{"toolName":"mcp__aos__propose_skill_patch"}},"toolCallId":"toolu_01PNddN6x7QwvBX7qA65XBj5","sessionUpdate":"tool_call_update","rawInput":{"old":"1. Name the module file in CamelCase, e.g. `DataLoader.py`."}}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"_meta":{"claudeCode":{"toolName":"mcp__aos__propose_skill_patch"}},"toolCallId":"toolu_01PNddN6x7QwvBX7qA65XBj5","sessionUpdate":"tool_call_update","rawInput":{"old":"1. Name the module file in CamelCase, e.g. `DataLoader.py`.","new":"1. Name the module file in snake_case, e.g. `data_loader.py`."}}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"_meta":{"claudeCode":{"toolName":"mcp__aos__propose_skill_patch"}},"toolCallId":"toolu_01PNddN6x7QwvBX7qA65XBj5","sessionUpdate":"tool_call_update","rawInput":{"old":"1. Name the module file in CamelCase, e.g. `DataLoader.py`.","new":"1. Name the module file in snake_case, e.g. `data_loader.py`.","reason":"Three user corrections all show project uses snake_case naming, not CamelCase. Agent created ConfigReader.py, ResultCache.py, and EmailSender.py in CamelCase, each time corrected to snake_case. Skill must teach the actual convention."}}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"usage_update","used":25565,"size":200000,"_meta":{"_claude/model":"claude-haiku-4-5-20251001"}}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"sessionUpdate":"usage_update","used":25565,"size":200000,"_meta":{"_claude/rateLimit":{"status":"allowed","resetsAt":1793706000,"rateLimitType":"five_hour","overageStatus":"rejected","overageDisabledReason":"org_level_disabled","isUsingOverage":false,"unifiedWindows":{"five_hour":{"utilization":0.69,"resetsAt":1793706000},"seven_day":{"utilization":0.19,"resetsAt":1794009600}}},"_claude/model":"claude-haiku-4-5-20251001"}}}}
< {"jsonrpc":"2.0","id":1,"method":"session/request_permission","params":{"toolCall":{"toolCallId":"toolu_01PNddN6x7QwvBX7qA65XBj5","name":"mcp__aos__propose_skill_patch","status":"pending","rawInput":{"old":"1. Name the module file in CamelCase, e.g. `DataLoader.py`.","new":"1. Name the module file in snake_case, e.g. `data_loader.py`.","reason":"Three user corrections all show project uses snake_case naming, not CamelCase. Agent created ConfigReader.py, ResultCache.py, and EmailSender.py in CamelCase, each time corrected to snake_case. Skill must teach the actual convention."},"title":"mcp__aos__propose_skill_patch","kind":"other","content":[],"_meta":{"claudeCode":{"toolName":"mcp__aos__propose_skill_patch","mcpServer":{"name":"aos","source":"dynamic"}}}},"options":[{"optionId":"allow-once","name":"Yes","kind":"allow_once"},{"optionId":"allow-with-updates","name":"Yes, and don't ask again for Propose Skill Patch commands","kind":"allow_always"},{"optionId":"reject","name":"No","kind":"reject_once"}],"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496"}}
> {"jsonrpc": "2.0", "id": 1, "result": {"outcome": {"outcome": "selected", "optionId": "allow-once"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"_meta":{"claudeCode":{"toolResponse":[{"type":"text","text":"{\n  \"ok\": true,\n  \"proposal\": \"20261103-090203-skill-3a56cf\"\n}"}],"toolName":"mcp__aos__propose_skill_patch"}},"toolCallId":"toolu_01PNddN6x7QwvBX7qA65XBj5","sessionUpdate":"tool_call_update"}}}
< {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"c5f3fed9-939a-46c1-90e8-23e319b3b496","update":{"_meta":{"claudeCode":{"toolName":"mcp__aos__propose_skill_patch"}},"toolCallId":"toolu_01PNddN6x7QwvBX7qA65XBj5","sessionUpdate":"tool_call_update","status":"completed","rawOutput":[{"type":"text","text":"{\n  \"ok\": true,\n  \"proposal\": \"20261103-090203-skill-3a56cf\"\n}"}],"content":[{"type":"content","content":{"type":"text","text":"{\n  \"ok\": true,\n  \"proposal\": \"20261103-090203-skill-3a56cf\"\n}"}}]}}}
```
