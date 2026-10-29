# Spike: the learning loop with a real agent tool

Throwaway end-to-end check of the learning loop (spec `docs/superpowers/specs/2026-10-09-learning-loop-design.md`). It is not product code.

```bash
~/.agenticos/venv/bin/python spikes/learning-e2e/run.py --tool claude   # or --tool kiro
```

The script runs in a throwaway `AOS_HOME` with two demo projects, `api` and `web`, and needs about 15 minutes and real tokens:
1. **Feature 1 on api:** the user steers a team convention ("every new function gets a CHANGELOG.md entry"), and a learner turns it into a lesson.
2. **Feature 2 on api, no steer:** the lesson is in the worker's context, and the worker follows it on its own.
3. **Confirm:** `aos lessons confirm` raises the lesson to 0.8.
4. **Feature 3 on web:** the same convention; its lesson links the api lesson (`same_as` plus `general_rule`).
5. **Confirm the web lesson:** one org memory proposal appears in the inbox. `aos inbox apply` adds the rule to org memory.

## Runs

- `REPORT-2026-10-29-claude-1.md`: 11/12. The loop worked end to end, and the run found three problems:
  - **A bug predating the branch:** a chained `verify_command` ran only its first part.
  - **Workers were never told the project's check.**
  - **The learner wrote generic process advice** instead of the project fact.

  All three are fixed in `68737f2`.
- `REPORT-2026-10-29-claude-2.md`: 13/13, after the fixes. The lesson states the real convention, feature 2 followed it without a steer, and the rule was promoted to org memory.
