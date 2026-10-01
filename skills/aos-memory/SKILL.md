---
name: aos-memory
description: Decide what to save to agenticOS memory vs. a skill, and how to write it. Use when you learned a durable fact, finished a non-trivial task, hit a dead end, or the user corrected you.
---

# Saving what you learn

## Memory or skill?
- **Memory** = a fact that should be in context every session. One or two sentences.
  - `org`: true for every project (business rules, glossary, team-wide decisions). Staged for review.
  - `project`: true for this repo (architecture decisions, quirks, owners, environments).
  - `user`: this person's preferences.
- **Skill** = a procedure for a class of task, loaded only when relevant. Steps, commands, pitfalls.
- **Neither**: task progress, logs, things the code or git history already says.

## Writing memory
- One fact per entry, with the reason: "Invoices are issued on the 1st because finance closes on the last business day."
- Before adding, `memory_read` the scope; `memory_replace` an outdated entry instead of adding a contradicting one.
- When full: merge related entries, remove stale ones, then retry.

## Writing a skill
- `skill_list` first; patch an existing skill instead of creating a near-duplicate.
- Lessons, not logs: the steps in order, the commands that work, what the result should look like, and each pitfall as a general rule plus one clause of why.
- Frontmatter `description` says what the skill does and when to use it; that is what triggers it.
- Scope a project-specific skill with `metadata: {aos: {projects: [<slug>]}}`.
