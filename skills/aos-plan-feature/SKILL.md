---
name: aos-plan-feature
description: Plan a feature that spans several linked projects into an agenticOS board feature (brief, shared contract, tasks with dependencies) so parallel workers can build it. Use when the user describes work touching more than one project, or asks to "plan a feature" for the board.
---

# Plan a multi-project feature

1. Clarify the goal with the user: what changes for whom, what is out of scope, what "done" looks like. One question at a time.
2. `project_list`, then `project_context` for each candidate project. Orient on each codebase with graphskill (`repo_map`, `search_symbols`) and `knowledge_search` for domain rules. Confirm with the user which projects are involved.
3. Draft the **brief**: goal, scope, non-goals, acceptance criteria, rollout notes.
4. Draft the **contract**, the only thing projects may assume about each other: endpoints (method, path, request and response shapes, status codes, errors), events (name, payload), schema changes, versioning and compatibility rules. Be concrete enough that two teams could build against it without talking.
5. Show both to the user and adjust until they approve.
6. `feature_create(slug, title, brief, contract)`.
7. Split into tasks, one project each, small enough for one worker session (about one PR):
   - Providers first: the side that implements a contract gets tasks the consumers depend on, but only where a consumer truly cannot proceed (tests against the contract can usually run in parallel).
   - Each spec says what to build, which contract sections it implements, and how to test it.
   - `task_create(project, title, feature, spec, depends_on=[...])`.
8. Show the plan as a list (`#id project title ← depends on`) and tell the user to start it with `aos board run --feature <slug>` and watch with `aos board`.
