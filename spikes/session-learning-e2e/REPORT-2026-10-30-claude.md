# Session learning end-to-end (claude)

- date: 2026-10-30T15:20
- result: **PASS** (6/6)

## Checks

- ✅ aos link installed the SessionEnd hook
- ✅ session 1 queued and learned from
- ✅ a session lesson at 0.4
- ✅ not in context yet
- ✅ seen again: a lesson at >= 0.5
- ✅ now in context

## Driver

```
15:19:22 ok   aos link installed the SessionEnd hook
15:19:38 session 5af9eb4e-4c81-4a3d-8d67-943ca1c3ecbd: created (0)
15:19:49 session 5af9eb4e-4c81-4a3d-8d67-943ca1c3ecbd: corrected (0)
15:20:13 queue: [{"id": "20261030-151948-claude-a71596", "status": "done", "tries": 0, "reason": "Use snake_case for module names, not PascalCase."}]
15:20:13 ok   session 1 queued and learned from
15:20:13 lessons: [{"trigger": "When creating a new module file in this project", "action": "Use snake_case for module names, not PascalCase \u2014 this project's convention.", "conf": 0.4}]
15:20:13 ok   a session lesson at 0.4
15:20:13 ok   not in context yet
15:20:27 session 026222ca-01d1-4e66-97f3-23acd12d801c: created (0)
15:20:38 session 026222ca-01d1-4e66-97f3-23acd12d801c: corrected (0)
15:20:56 lessons: [{"trigger": "When creating a new module file in this project", "action": "Use snake_case for module names, not PascalCase \u2014 this project's convention.", "conf": 0.5}]
15:20:56 ok   seen again: a lesson at >= 0.5
15:20:56 ok   now in context
```

## sessions.log (tail)

```
done: Use snake_case for module names, not PascalCase.
```
