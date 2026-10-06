---
name: aos-feature
description: Deliver a feature that spans several linked projects end to end from one conversation. Plan it with the user (brief, shared contract, tasks per project from the code graph), launch parallel agenticOS board workers (one per project, in their own worktrees), follow them without spending tokens, relay blockers and contract changes to the user and act on the answers, then report. Use when the user wants to plan, build or deliver work touching more than one project.
---

# Deliver a multi-project feature

You run the whole flow in this conversation. The user only steps in for decisions: approving the plan, answering blockers, deciding contract changes. **Never implement tasks yourself**, not even small ones. Board workers do the work, each in its project's own git worktree.

## 1. Plan (with the user)
1. Clarify the goal: what changes for whom, out of scope, what "done" means. One question at a time.
2. `project_list`, `project_context` for each candidate project, `knowledge_search` for domain rules. Confirm which projects are involved.
3. Read each involved project's code with `code_query(project, tool, arguments)`. It works for every linked project, not only this one:
   - `repo_map` to orient;
   - `search_symbols` / `search_semantic` for what the feature touches;
   - `read_symbol_body` / `callers` where the contract meets existing code.

   If a project has no graph, say so and continue from memory.
4. Draft the **brief**: goal, scope, non-goals, acceptance criteria.
5. Draft the **contract**, the only thing projects may assume about each other: endpoints (method, path, request and response shapes, status codes, errors), events, schema changes, compatibility rules.
6. Draft the **tasks**:
   - one project each, about one PR in size;
   - each spec names the real files and symbols, which contract sections it implements, and how to test it;
   - add dependencies only where a consumer truly cannot proceed without the provider.
7. Show the brief, contract and task list (`#n project title ← depends on`). Adjust until the user approves. **Do not create anything before approval.**

## 2. Create
8. `feature_create(slug, title, brief, contract)`, then one `task_create(project, title, feature, spec, depends_on)` per task.

## 3. Run
9. **In Kiro** you may offer the Workflows panel instead: call `feature_workflow(feature)` and tell the user to press **Run** on `aos-<feature>`. That workflow then does steps 10-12 itself; you are done after reporting the path. **Otherwise** (Claude Code, or the user prefers this chat), call `board_start(feature)` and continue.

## 4. Follow (loop until finished)
10. Call `board_wait(feature, cursor)`. It waits without spending tokens. Always pass back the `cursor` it returned last time; the first call has none. Then act on `reason`:
    - **attention**: for each item:
      - `blocked`: quote `blocker` and ask the user what to do.
      - `failed`: show `last_error`; ask retry (with guidance) or cancel.
      - `proposals`: show each `change` and `reason`; ask approve or reject.
      - `stuck`: a task waits on a failed or cancelled task (`waiting_on`); ask retry or cancel that task.

      Ask everything pending in one message, then **stop and wait for the user's reply**.
    - **stalled**: call `board_start(feature)`, then `board_wait` again.
    - **timeout**: still working. Give at most a one-line progress note (counts), then `board_wait` again.
    - **waiting_on_human**: nothing can run until the user answers what is listed in `pending`. Remind them briefly and stop.
    - **finished**: go to step 12.
11. When the user answers, act on each decision, then `board_start(feature)` and back to step 10:
    - `task_unblock(task, note=<their answer>)`;
    - `task_retry(task, note=<their guidance>)` or `task_cancel(task)`;
    - `proposal_decide(proposal, approve, reason)`. On approve, the contract gets a new version that every worker must re-read.

## 5. Finish
12. Report each task's result (first line), anything cancelled, and where the work is: `~/.agenticos/worktrees/<feature>/<project>` on branch `feature/<feature>`. Nothing is pushed; the user reviews and merges.
13. Offer a review pass. If wanted, add one task per project, depending on that project's tasks: "Review the feature branch against contract v<N>; fix what deviates; report findings". Then go back to step 10.
14. Save anything durable you learned about the projects with `memory_add` (project scope).
