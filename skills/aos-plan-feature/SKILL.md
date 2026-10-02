---
name: aos-plan-feature
description: Plan a feature that spans several linked projects into an agenticOS board feature (brief, shared contract, tasks with dependencies) so parallel workers can build it. Use when the user describes work touching more than one project, or asks to "plan a feature" for the board.
---

# Plan a multi-project feature

1. Clarify the goal with the user: what changes for whom, what is out of scope, what "done" looks like. One question at a time.
2. `project_list`, then `project_context` for each candidate project, and `knowledge_search` for domain rules. Confirm with the user which projects are involved.
3. **Read each involved project's code graph** before writing anything. Use `code_query(project, tool, arguments)`; it works for every linked project, not only the one this session runs in:
   - `code_query(p, "repo_map", {})` to orient;
   - `code_query(p, "search_symbols", {"query": "..."})` / `"search_semantic"` for the parts the feature touches;
   - `"callers"` / `"read_symbol_body"` where the contract meets existing code.
   If a project has no graph (the error says so), tell the user it should be linked with graphskill installed, and continue from project memory.
4. Draft the **brief**: goal, scope, non-goals, acceptance criteria, rollout notes.
5. Draft the **contract**, the only thing projects may assume about each other: endpoints (method, path, request and response shapes, status codes, errors), events (name, payload), schema changes, versioning and compatibility rules. Be concrete enough that two teams could build against it without talking.
6. Show both to the user and adjust until they approve.
7. `feature_create(slug, title, brief, contract)`.
8. Split into tasks, one project each, small enough for one worker session (about one PR):
   - Providers first: the side that implements a contract gets tasks the consumers depend on, but only where a consumer truly cannot proceed (tests against the contract can usually run in parallel).
   - Each spec says what to build, which contract sections it implements, the real files and symbols to change (from the code graph), and how to test it.
   - `task_create(project, title, feature, spec, depends_on=[...])`.
9. Show the plan as a list (`#id project title ← depends on`) and ask the user to confirm it.

## Hand off: workers build it, not you

**Do not implement the tasks yourself**, not even small ones. You are the planner. Each task is done by its own headless worker (Claude Code or Kiro), in that project's own git worktree, with the feature context plus that project's context.

10. On the user's go-ahead, call `board_start(feature)`. It launches the workers in the background and returns immediately. They keep going even if this session ends.
11. Follow progress with `board_status(feature)` when the user asks, or after a natural pause. Report it briefly: what is done (with each result's first line), what is running, and anything that needs the user:
    - `blocked`: relay the reason. The user answers with `aos task unblock <id> --note "..."`.
    - `pending_proposals`: a worker wants to change the contract. Show the change; the user decides with `aos feature approve|reject <id>`.
    - `stuck`: a dependency failed or was cancelled. Suggest `aos task retry <id>`.
12. After the user unblocks, retries or approves something, call `board_start(feature)` again. The dispatcher stops on its own when nothing more can run.
13. When every task is `done`, tell the user where the work is (`~/.agenticos/worktrees/<feature>/<project>`, branch `feature/<feature>`, never pushed) so they can review and merge.
