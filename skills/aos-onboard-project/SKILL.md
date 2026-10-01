---
name: aos-onboard-project
description: Build the agenticOS project memory for a newly linked repo by combining the code graph with a short interview. Use right after `aos link`, or when project memory is empty.
---

# Onboard a project into agenticOS

1. `memory_read("project")`. If it already has entries, ask whether to refresh or stop.
2. Orient on the code with graphskill: `repo_map()`, then `module_overview()` on the 2-3 biggest areas. Do not read whole files.
3. `knowledge_search` for the project name and its main domain terms; `project_list` to see related projects.
4. Ask the user at most five questions, one at a time, about what the code cannot tell you:
   - What business problem does this service solve, and for whom?
   - Which other projects or external systems does it depend on?
   - Which rules or invariants must never be broken?
   - Where does it run (environments, deploy path), and who owns it?
   - What has bitten people before?
5. Save 4-8 entries with `memory_add("project", ...)`: purpose, dependencies, invariants, environments, gotchas. Keep the total under the cap.
6. Facts that are true beyond this project go to `memory_add("org", ...)`, which is staged for review.
7. Suggest a one-line description for `projects.yaml` and show the user what was saved.
