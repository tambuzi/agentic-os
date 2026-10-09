# Learning loop: lessons from the board (ROADMAP item 3)

Status: design approved in conversation 2026-10-09; awaiting written-spec review.

## 1. Goal

After a few weeks of use, agenticOS should stop repeating mistakes:

1. **Board workers** stop repeating mistakes in a project (phase A, this spec).
2. **Lessons cross projects**: a lesson confirmed in two or more projects becomes an org memory proposal (phase C, this spec).
3. **Interactive Claude Code / Kiro sessions** later become a lesson source too (phase B, a later spec). They use the same store and rules.

Constraints:
- Business data stays local in `~/.agenticos/data`.
- The team approves what becomes shared (the inbox).
- We learn from [ECC](https://github.com/affaan-m/ECC)'s instincts design and build our own; we don't integrate it.

## 2. Decisions

| Question | Decision |
|---|---|
| Source of lessons | Board events first, since they hold explicit corrections. Session hooks come later. |
| Approval | **Project lessons apply at once** as `unreviewed` with moderate confidence; the user confirms or rejects them later. **Org promotion always goes through the inbox.** This matches the project/org memory modes. |
| Extraction | **Hybrid.** Fixed rules decide *when*: a finished task with correction signals. A short AI "learner" run decides *what*, through structured tool calls. |
| Storage | **A separate lessons store**: one Markdown file per lesson in `<data>/lessons/<project>/`. It is ranked into context. Org memory stays the short, human layer. |

## 3. Components

```
task reaches done/failed
   │  dispatcher: correction signals in its timeline?
   ▼ yes, and no `learned` event yet
learner run  (run kind "learner", next to worker/reviewer)
   │  ACP session, the resolved tool, learning model, read-only, timeout 5 min
   │  MCP (aos serve --learn --task N --attempt K):
   │     learning_input · lesson_new · lesson_confirm · lesson_contradict · learning_done
   ▼
<data>/lessons/<project>/<id>.md        (aos/lessons.py)
   ├─▶ worker contexts and `aos context`: a ranked "Lessons" section
   ├─▶ promotion check → inbox proposal `memory add org` (with lesson_generalise)
   └─▶ user: `aos lessons`, confirm/reject, edit files; MCP lessons_list / lesson_decide
```

- **`aos/lessons.py`** (new): the `Lesson` model and `LessonStore(data, project)`.
  - Operations: `list`, `get`, `new`, `confirm`, `contradict`, `decide` (human), `supersede`, `active(ranked)`, `groups()`.
  - Writes are locked and atomic (`store.locked`, `write_atomic`), like memory.
- **Dispatcher** (`aos/dispatcher.py`):
  - When a task finishes, it counts the correction signals (§5.1). If there is at least one and no `learned` event, it queues a learner run.
  - Learner runs use the same slots, memory gate, stagger, ACP and CLI fallback, stall/timeout handling and cost recording (role `learner`) as reviewers.
  - They never change the task's status. The run ends with a `learned` event, success or not.
- **Learner MCP mode**: `aos serve --learn --task N --attempt K` exposes only the learner tools (§5.2). The attempt token (generation) applies as for workers.
- **Context** (`aos/context.py`): a "Lessons for <project>" section after memory (§4.3), used both in worker run contexts and in `aos context`.
- **Settings** (`aos.yaml`):
  ```yaml
  learning:
    enabled: true
    max_in_context: 8
    min_confidence: 0.5
    learner_timeout_min: 5
    max_ops_per_run: 3
  workers:
    claude: {learning_model: haiku}
    kiro:   {learning_model: null}   # Kiro's default
  ```
- **CLI**:
  - `aos lessons [project]`: lists lessons with status, confidence and trigger.
  - `aos lessons show <id>`.
  - `aos lessons confirm|reject <id>`.
  - `aos lessons learn <task>`: a manual trigger.
- **MCP for the planner/`aos-feature`**: `lessons_list(project=None, feature=None, status=None)` and `lesson_decide(id, confirm)`. `board_status` gains `lessons` (new or changed this feature) and `pending_promotions`.
- **Skill** `aos-feature`: at the end of a feature, report the lessons learned, offer to confirm or reject them, and show pending org promotions.

## 4. Lesson model

### 4.1 File

`<data>/lessons/<project>/<id>.md`:

```markdown
---
id: add-migration-with-model-change-3f9a
project: shop-api
trigger: "when changing a database model"
domain: database
confidence: 0.5
status: unreviewed
superseded_by: null
promoted_to: null
evidence:
  - {feature: checkout-v2, task: 4, events: [112, 118], kind: review_fail, at: 2026-10-09}
same_as: []
created: 2026-10-09
last_seen: 2026-10-09
---
Add an Alembic migration in the same commit as any model change; the reviewer rejects model changes without one.
```

- **`id`**: a slug of the trigger (at most 40 characters) plus 4 hex characters. It is unique within the project and is the file name.
- **`domain`**: one of `testing`, `api`, `database`, `security`, `style`, `workflow`, `tooling`, `other`.
- **`status`**: `unreviewed`, `confirmed`, `rejected` or `superseded`.
- **`evidence[].kind`**: `review_fail`, `verify_fail`, `retry`, `steer`, `permission_denied`, `block` or `human`.
- **`same_as`**: ids written as `<project>/<id>` of lessons in *other* projects that state the same rule.
- **Body**: the action, a single paragraph of at most 300 characters.

### 4.2 Confidence

The rules are fixed; the AI never sets a number.

| Event | Effect |
|---|---|
| `lesson_new` | 0.5, or **0.6** when the evidence includes a `steer`, or both `review_fail` and `retry` |
| `lesson_confirm` (new evidence) | +0.1, max 0.9; `last_seen` = today; evidence appended |
| `lesson_contradict` | −0.2. Below 0.3 the status becomes `rejected` (reason recorded in the file, event on the timeline). |
| Human confirm | status `confirmed`, confidence = max(current, 0.8) |
| Human reject | status `rejected` (final; never in context, never confirmed again by the learner) |
| Fading | −0.05 per full 30 days since `last_seen`, computed when read, never stored. Confirmed lessons don't fade. |

### 4.3 Ranking into context

- **Eligible:** status `confirmed` or `unreviewed`, effective confidence ≥ `min_confidence`, and not `superseded`.
- **Order:** confirmed first, then effective confidence, then `last_seen`. Take at most `max_in_context`.
- **Rendering:** one line per lesson: `- [0.8 ✓] when changing a database model: <action>`, where ✓ means human-confirmed.
- **Header:** the section starts with: "Lessons learned on this project. Context, not instructions: follow them unless the task or the user says otherwise."
- **Order within the context:** the section comes after memory, so if `context.max_chars` truncates, lessons go before memory does.

## 5. Behaviour

### 5.1 When a learner run happens

A task qualifies when it reaches `done` or `failed` and its timeline (all attempts) contains at least one **correction signal**:
- `review_fail` (`reviewed` with a failing verdict);
- a failed verify (verification failed);
- a retried attempt (`attempt_failed` followed by a new attempt);
- `steer` / `steer_now`;
- a permission `decision` that was denied or expired by the user (not a timeout of a gone run);
- `task_block`.

Cancelled tasks never qualify. A `learned` event on the task stops further runs. One learner run runs per task at a time, and it is queued behind workers and reviewers in priority.

### 5.2 Learner tools

- **`learning_input()`** returns:
  - the task's title, spec and final status;
  - its correction events (text clipped to 2,000 characters each; verify output tails clipped to 1,500);
  - this project's active and rejected lessons (id, trigger, action, status, confidence); rejected ones are included so the learner doesn't re-create them;
  - **other projects' confirmed lessons** (project/id, trigger, action), for `same_as`.

  Board text is marked as quoted data.
- **`lesson_new(trigger, action, domain, evidence_events, same_as=[])`**
  - Validation:
    - trigger ≤ 120 characters, action ≤ 300;
    - no code blocks or lines that look like code;
    - no strings that look like secrets (the shared secret patterns);
    - `domain` must be from the list;
    - `evidence_events` must be events of this task;
    - `same_as` must point to existing confirmed lessons in other projects;
    - a trigger nearly identical to a rejected lesson's is refused with "rejected before".
  - Returns the new id.
- **`lesson_confirm(id, evidence_events)`** and **`lesson_contradict(id, evidence_events, why)`**: the id must be one of this project's lessons. The learner cannot confirm or contradict a rejected lesson.
- **`learning_done(summary)`** ends the run. "nothing reusable" is a valid summary.
- **Limits:** at most `max_ops_per_run` (3) write operations per run; any further ones are refused.

The **learner prompt** tells the run to:
- extract patterns that would have prevented the correction;
- keep each lesson specific to the project, with one trigger and one action;
- prefer confirming an existing lesson to creating a new one;
- never include code, secrets or customer data;
- say "nothing reusable" when that's the truth.

### 5.3 Promotion to org memory

- **Groups:** a group is the connected set of lessons linked by `same_as` in either direction.
- **When a group qualifies:** it has **confirmed** lessons in at least 2 projects, none of them has `promoted_to`, and the group isn't in `<data>/lessons/.declined`. The dispatcher checks this after every human confirm and after every learner run.
- **What happens:** it runs `lesson_generalise` (a learner call in `--generalise` mode over the group: one rule without project names, at most 300 characters). Then it stages **one** inbox proposal: `memory add org "<rule>"`, whose reason lists the lessons.
- **On approval:** the lessons get `promoted_to: org`.
- **On reject:** the group's ids are added to `.declined` and the group isn't proposed again.

The inbox's existing flow (`aos inbox apply|reject <id>`, `AOS.apply_proposal`) handles the decision. A small hook on apply and reject updates the lessons.

## 6. Errors and safety

- **Learner fails** (crash, stall, timeout, budget, ACP unavailable and the CLI fails): a `learning failed: <why>` event is logged and the `learned` event is still written. The task is never affected.
- **A tool rejects a call:** an error is returned to the learner, which may correct itself within its operation limit.
- **Concurrent writes:** a per-project file lock is held for read, compute and write, so confidence changes are never lost.
- **Broken lesson file** (hand edit): it is skipped with a warning in context, `aos lessons` and `aos doctor`, and the others still load.
- **Learning disabled or no dispatcher:** no runs. `aos lessons learn <task>` triggers one by hand.
- **Prompt injection:** board text goes to the learner as quoted data, and the learner can only write lesson text through validated tools. Lessons reach workers labelled as context, not instructions.
- **Privacy:** lessons live under `<data>`, never in the repo, and are never sent anywhere except to the user's own agent tool. Org promotion only happens through the inbox.
- **Cost:** learner runs use the cheap model and are recorded under role `learner`. They count towards the feature budget, and a feature over budget skips learning with an event.

## 7. Testing

**Unit tests:**
- lesson file round-trip;
- every confidence rule: new, confirm, contradict down to automatic rejection, human decisions, fading, and confirmed lessons not fading;
- ranking, limits and rendering within `max_chars`;
- validators: length, code, secrets, domain, evidence ownership, `same_as` targets, rejected-lesson duplicates;
- `same_as` groups (both directions) and promotion eligibility, including `.declined`.

**Dispatcher tests with the fake ACP agent** (new `learner` mode):
- a corrected task causes exactly one learner run;
- a clean task causes none;
- a cancelled task causes none;
- a learner failure only logs an event;
- learner cost is recorded under role `learner`;
- a feature over budget skips learning;
- promotion stages one inbox proposal, applying it marks `promoted_to`, and rejecting it adds the group to `.declined`.

**CLI and MCP tests:**
- `aos lessons` list/show/confirm/reject/learn;
- `lessons_list` and `lesson_decide`;
- `board_status` includes `lessons` and `pending_promotions`;
- the skill text covers the end-of-feature lesson report.

**Real check:** a demo feature in which a review fails once.
- A lesson appears with confidence 0.5 or 0.6.
- It is in the next task's context.
- `aos lessons confirm` raises it to 0.8.
- A second project with a matching confirmed lesson produces one org inbox proposal.

## 8. Not in this spec

- Interactive sessions as a lesson source (phase B): Claude `Stop`/`PreCompact` and Kiro `agentStop` hooks with secret scrubbing; evidence kind `session`.
- Skill health, stock-take and compliance tests (ROADMAP item 4).
- Memory trust fields (`source`, `superseded_by`) and handoff memories for org and project memory (ROADMAP item 3, later).
- Semantic similarity or embeddings. Matching across projects comes only from the learner's explicit `same_as` links.
