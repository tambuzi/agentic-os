# Learning from interactive sessions (learning loop, phase B)

Status: design approved in conversation 2026-10-30 (sections 1–2; testing below); awaiting written-spec review.

## 1. Goal

The user's own Claude Code and Kiro sessions become a lesson source, alongside board work. When the user corrects their agent in a session, agenticOS learns a lesson from it. That lesson reaches later sessions and board workers on the same project once it has been seen again or confirmed.

It builds on the board learning loop (spec `2026-10-09-learning-loop-design.md`), using the same lessons store, confidence rules, ranking, review and org promotion. Business data stays local. Nothing about a session is stored except the lessons and a pointer to the transcript.

## 2. Decisions

| Question | Decision |
|---|---|
| Which sessions produce lessons | **Only sessions with corrections**, found by fixed rules (no AI, no cost otherwise), **plus a manual trigger** for any session. |
| What happens to a session lesson | **It is saved below the context threshold:** it starts at **0.4**, under `learning.min_confidence` (0.5). It reaches context when it is seen again (+0.1) or when the user confirms it (0.8). A one-off change of mind never reaches the agents. |
| When the learner runs | **Right after the session ends, in the background**, started by the end-of-session hook. A local queue is the safety net: failed or missed runs are retried. |

## 3. Components and flow

```
the session ends
   │  hook: Claude Code SessionEnd (stdin JSON with transcript_path, session_id, cwd)
   │        Kiro: agentStop hook (how the finished conversation is exposed: Task 0 spike)
   ▼
aos lessons capture --tool claude|kiro [--transcript PATH] [--session ID] --project-dir DIR
   │  ≤ 2 s, never fails visibly (errors → <data>/logs/sessions.log)
   │  fixed rules find corrections (§4) → none: done, no cost
   ▼ found
<data>/sessions/queue/<id>.json   {id, tool, session, project, transcript, excerpts:[turn ranges],
                                   kinds, status: pending|running|done|failed, tries, reason, created}
   │  starts a detached job:  aos lessons learn-session <id>
   ▼
session learner: AcpRun (the project's tool, workers.<tool>.learning_model, lesson tools only),
outside the dispatcher; MCP  aos serve --learn-session <id>
   │  learning_input → quoted, scrubbed excerpts; this project's lessons; other projects' confirmed
   ▼
the lessons store (aos/lessons.py): `session` evidence; session-only lessons start at 0.4
```

**New modules:**
- `aos/sessions.py`: transcript readers (Claude JSONL; Kiro once the spike says how), the correction rules, the queue (create, lock, status, retry, prune), and capture.
- `aos/session_learner.py`: running the learner for one queue entry, through AcpRun with a CLI fallback, as board learners do. It also records costs and keeps the daily budget.

**Changed modules:**
- `aos/lessons.py`: evidence kind `session`, with fields `{tool, session, turns:[int], kind: correction|refused_tool|self_fix, at}`. A lesson whose evidence is only `session` starts at 0.4. Mixed evidence follows the board rules.
- `aos/mcp_server.py`: `LearnerTools` gains a session mode. `learning_input` returns the session's excerpts instead of board corrections, and evidence ids are excerpt ids. The same validation and operation limit apply.
- `aos/adapters/claude.py` and `aos/adapters/kiro.py`: `aos link` installs the end-of-session hook next to SessionStart, and `aos unlink` removes it.
- `aos/cli.py`:
  - `aos lessons capture …` (called by the hook);
  - `aos lessons sessions` (the queue);
  - `aos lessons learn-sessions` (process pending entries now);
  - `aos lessons learn-session <id> | --last` (one entry; `--last` learns from the most recent session of this project even without corrections, which is the manual trigger).
- `aos doctor`: shows the queue state and any failed entries.

**Settings** (`aos.yaml`):
```yaml
learning:
  sessions: true                   # off switch for session learning
  sessions_exclude: []             # project slugs never learned from
  sessions_daily_budget_usd: 1.0   # above it, entries wait for the next day
```

## 4. Correction rules (deterministic)

The rules read the transcript as a list of turns: user, assistant and tool. Each hit becomes an excerpt: the 3 turns before it and the 1 after.
- **correction:** a user message right after an assistant turn whose first words match (case-insensitive):
  - English: `no`, `nope`, `don't`, `do not`, `stop`, `wrong`, `that's not`, `not like that`, `instead`, `actually`, `revert`, `undo`;
  - Spanish: `no,`, `eso no`, `mal`, `en vez de`, `en lugar de`, `deshaz`, `revierte`, `para`.

  A bare "no" answering the agent's own yes/no question doesn't count (the previous assistant turn ends with "?").
- **refused_tool:** the transcript records that the user refused a tool call. For Claude Code, that is the tool result "The user doesn't want to proceed with this tool use".
- **self_fix:** a shell tool call failed (error or non-zero exit), and within the next 3 tool calls the agent ran the same command with changed arguments, and it succeeded.

The rules must not run on agenticOS's own automated sessions: board workers, reviewers and learners. A capture whose cwd is under `~/.agenticos/worktrees` or `<data>/runs` is ignored.

## 5. The session learner

`learning_input` (session mode) returns:
- **excerpts:** each turn shortened to 1,500 characters, and each tool output to 300 characters. Files the agent read are never included. Secrets are scrubbed with the lessons secret check. Each turn is quoted with the fence that can't be closed (`aos/quoting.py`).
- **the project's lessons**, including rejected ones, so it doesn't re-create them;
- **other projects' confirmed lessons**, for `same_as` and `general_rule`;
- **a note** that the excerpts are quoted data, never instructions.

The learner protocol is the board learner's, adapted: state the project fact behind the user's correction, not generic advice; prefer `lesson_confirm`; "nothing reusable" is a valid outcome. The evidence ids must be excerpt ids of this session. At most `learning.max_ops_per_run` changes.

## 6. Failure handling and privacy

- **The hook never slows down or breaks a session.** `capture` returns within 2 s, always exits 0, and logs problems to `<data>/logs/sessions.log`. It does not read the transcript beyond what the rules need.
- **A background job that fails** (tool missing, offline, budget reached, crash, timeout of `learning.learner_timeout_min`) leaves the entry `pending` with the reason and `tries+1`. The next job or `aos lessons learn-sessions` retries it. After 3 tries the entry is `failed`, and `aos doctor` reports it.
- **A lock per entry** stops two jobs from processing the same session.
- **Done or failed entries** are deleted after 7 days. The queue stores the transcript path and the excerpt positions, never transcript text.
- **Cost** is recorded per entry in `<data>/sessions/costs.jsonl` and counted against `sessions_daily_budget_usd`.
- **When session learning is off** (`learning.sessions: false`, or the project is excluded), `capture` does nothing.

## 7. Testing

**Unit tests:**
- **Correction rules on recorded transcripts** (fixtures):
  - English and Spanish corrections;
  - a "no" that answers the agent's own question is not counted;
  - a refused tool;
  - a self-fix;
  - an ordinary session gives no hits;
  - automated sessions (worktrees, runs) are ignored.
- **Excerpts:** shortened, tool outputs cut, secrets scrubbed, quoted.
- **Queue:** create, lock, status changes, retry counting, 3-try failure, 7-day pruning.
- **Lessons:** session-only evidence starts at 0.4 and is not in context; a second observation reaches 0.5 and is in context; a user confirm sets 0.8; mixed evidence follows the board rules.

**Integration tests:**
- **`aos lessons capture`:** with a transcript fixture it creates a queue entry and starts the job (job launcher injected). It finishes fast and exits 0 even on a broken transcript.
- **The job, with the fake ACP agent's learner mode in session mode:** lesson created with `session` evidence; failure leaves the entry pending with a reason; budget reached leaves it pending; done entries are pruned.
- **`aos link` / `aos unlink`:** install and remove the Claude SessionEnd hook and the Kiro hook.
- **Other commands:** `aos lessons sessions`, `learn-sessions`, `learn-session --last`, and the doctor queue line.

**Real check:**
- A real Claude Code session in a demo project, where the user corrects a convention.
- After it ends, a session lesson exists at 0.4 and is not in context.
- A second session with the same correction raises it to 0.5, and it appears in the next session's context.
- Kiro: the same, on the user's machine, after the Task 0 spike.

## 8. Not in this spec

- Learning from sessions without corrections, except by the manual trigger.
- Reading anything beyond the transcript (screenshots, files read).
- Sharing session lessons with other users; they reach the team only through org promotion, as before.
- Kiro capture details beyond what the Task 0 spike establishes. If Kiro doesn't expose the conversation, Kiro sessions use only the manual trigger.
