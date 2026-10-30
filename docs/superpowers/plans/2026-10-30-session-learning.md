# Session Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native, as for earlier plans) to implement this plan task-by-task, test-first. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A Claude Code (and later Kiro) session that ends after the user corrected the agent produces lessons through a short background learner. They start below the context threshold and reach context once seen again or confirmed.

**Architecture:**
- **`aos/sessions.py`:** reads transcripts, applies the fixed correction rules, builds scrubbed and quoted excerpts, and owns the local queue and `capture`.
- **`aos/session_learner.py`:** runs the learner for one queue entry outside the dispatcher (AcpRun, CLI fallback, retries, daily budget).
- **Lessons:** gain a `session` evidence kind, and `LearnerTools` gains a session mode.
- **Hooks:** `aos link` installs the end-of-session hook.

**Tech Stack:** Python ≥ 3.11, existing aos modules, pytest, the fake ACP agent.

**Spec:** `docs/superpowers/specs/2026-10-30-session-learning-design.md`

## Global Constraints

- **Settings:** `learning.sessions: true`, `learning.sessions_exclude: []`, `learning.sessions_daily_budget_usd: 1.0`. The learner model is `workers.<tool>.learning_model`, and the timeout is `learning.learner_timeout_min`.
- **Session-only lessons start at 0.4.** Mixed evidence follows the board rules. The context threshold `learning.min_confidence` stays 0.5.
- **`capture` limits:** it returns within 2 s, always exits 0, and logs problems to `<data>/logs/sessions.log`. It never runs for sessions whose cwd is under `<home>/worktrees` or `<data>/runs`.
- **Excerpts:** the 3 turns before each hit and the 1 after. Each turn is clipped to 1,500 characters and each tool output to 300. Secrets are scrubbed with `lessons.contains_secret` patterns, and the text is quoted with `aos/quoting.quoted`. File contents read by the agent are never included.
- **Queue:** `<data>/sessions/queue/<id>.json`, status `pending | running | done | failed`. At most 3 tries; done and failed entries are pruned after 7 days. It stores the transcript path and the turn indexes, never transcript text.
- **Correction words:**
  - English (first words of a user message, case-insensitive): `no`, `nope`, `don't`, `do not`, `stop`, `wrong`, `that's not`, `not like that`, `instead`, `actually`, `revert`, `undo`.
  - Spanish: `no,`, `eso no`, `mal`, `en vez de`, `en lugar de`, `deshaz`, `revierte`, `para`.
  - A bare "no" or "nope" right after an assistant turn ending in "?" doesn't count.
- **Refused tool:** a tool_result containing "The user doesn't want to proceed with this tool use". **Self-fix:** a failed tool call (`is_error`) followed within 3 tool calls by the same tool with changed input that succeeds.

## Review Focus

1. **A huge or malformed transcript must not slow or break the session's end.** `capture` reads at most the last 4 MB and exits 0 within 2 s. Pinned by `test_capture_is_fast_on_a_huge_transcript` and `test_capture_never_fails` (Task 2).
2. **The same session captured twice** (SessionEnd after a resume or `/clear`) must not create a second queue entry for already-seen turns. Pinned by `test_same_session_is_queued_once_per_new_turns` (Task 2).
3. **Board workers, reviewers and learners must never trigger session learning.** Pinned by `test_automated_sessions_are_ignored` (Task 2).
4. **No transcript text or secret may land in the queue or the lessons.** Pinned by `test_queue_stores_no_transcript_text` (Task 2) and `test_excerpts_are_scrubbed_and_quoted` (Task 1).
5. **Two background jobs must not process the same entry.** Pinned by `test_two_jobs_one_entry` (Task 4).

---

### Task 0: Kiro capture spike (on the user's Kiro machine)

**Files:** `spikes/kiro-session-capture/probe.py` + `README.md` (throwaway).

- [ ] Write a probe the user runs in a Kiro project. It installs a temporary `agentStop` hook (IDE `.kiro/hooks/*.kiro.hook` with `when.type: agentStop` and `then.type: runCommand`, plus the CLI agent `hooks.stop` form) that dumps its stdin and environment to a file.
- [ ] It also lists where `kiro-cli` stores conversations (`~/.kiro`, `~/Library/Application Support/Kiro*`; it greps for a known phrase the user types).
- [ ] The README asks the user to run it and commit the findings.
- [ ] Until the findings arrive, Kiro sessions support only the manual trigger (`learn-session --last` for Kiro reports "Kiro capture not available yet").
- [ ] Commit `spike: probe how Kiro exposes a finished conversation`.

### Task 1: Transcripts, correction rules and excerpts

**Files:** create `aos/sessions.py` (part 1), `tests/fixtures/sessions/*.jsonl`; test `tests/test_sessions_rules.py`.

**Interfaces (produced):**
```python
@dataclass
class Turn:
    index: int; role: str            # "user" | "assistant" | "tool"
    text: str                        # user/assistant text; tool: output (or "")
    tool: str | None = None          # tool name for assistant tool_use / tool results
    input: str | None = None         # tool_use input as compact JSON
    error: bool = False              # tool_result is_error
    refused: bool = False            # the user refused this tool call

def read_claude_transcript(path: Path, max_bytes: int = 4_000_000) -> list[Turn]
    # skips isMeta, isSidechain, non user/assistant entries; user string content = user turn;
    # tool_result blocks = tool turns (text = content, error = is_error, refused = marker text)
@dataclass
class Hit:
    index: int; kind: str            # "correction" | "refused_tool" | "self_fix"
def find_corrections(turns: list[Turn]) -> list[Hit]
def excerpts(turns: list[Turn], hits: list[Hit]) -> list[dict]
    # [{"id": n, "kind", "turns": [{"index", "role", "text": quoted+scrubbed+clipped}]}]
```

- [ ] **Tests:**
  - `test_english_and_spanish_corrections` (fixtures `correction_en.jsonl`, `correction_es.jsonl`);
  - `test_no_answering_the_agents_question_is_not_a_correction`;
  - `test_refused_tool`;
  - `test_self_fix` (a failed `npm test`, then a successful `npm test -- --runInBand`);
  - `test_ordinary_session_has_no_hits`;
  - `test_meta_and_sidechain_entries_are_skipped`;
  - `test_excerpts_are_scrubbed_and_quoted` (a turn with `api_key = sk-…` and `>>>`: no secret in the output, fences neutralised, turn ≤ 1,500, tool output ≤ 300);
  - `test_reader_only_reads_the_tail_of_a_huge_file`.
- [ ] RED, implement, GREEN, run the full suite, commit `feat(sessions): read transcripts and find corrections`.

### Task 2: Queue and `aos lessons capture`

**Files:** modify `aos/sessions.py` (part 2), `aos/cli.py`, `aos/config.py` (settings); test `tests/test_sessions_capture.py`.

**Interfaces (produced):**
```python
def queue_dir(data: Path) -> Path
def enqueue(data, tool, session, project, transcript, hits, start_index) -> dict | None   # None when nothing new
def entries(data, status: str | None = None) -> list[dict]
def claim(data, entry_id) -> dict | None          # pending → running under a lock; None if taken
def finish(data, entry_id, ok: bool, reason: str = "") -> None   # done | pending(tries+1) | failed (3rd)
def prune(data, days: int = 7) -> int
def capture(aos_home, data, tool, transcript, session, project_dir, settings, launch=None) -> str
    # returns a short status; never raises; `launch(entry_id)` starts the job (default: detached
    # Popen of `aos lessons learn-session <id>`); dedupes by (tool, session) using the last
    # captured turn index stored in the entry
```
- **CLI:** `aos lessons capture --tool claude|kiro [--transcript PATH] [--session ID] [--project-dir DIR]`. For Claude it reads the hook's stdin JSON (`transcript_path`, `session_id`, `cwd`) when the flags are absent.
- **Project slug:** from `slug_for_path(project_dir)`. An unlinked directory → nothing happens.

- [ ] **Tests:**
  - `test_capture_queues_a_session_with_corrections_and_launches_the_job` (injected launcher records the id);
  - `test_capture_ignores_a_session_without_corrections`;
  - `test_capture_respects_settings_and_exclusions` (`sessions: false`; an excluded slug);
  - `test_automated_sessions_are_ignored` (cwd under worktrees or runs);
  - `test_same_session_is_queued_once_per_new_turns`;
  - `test_queue_stores_no_transcript_text` (the entry JSON has no turn text);
  - `test_capture_never_fails` (a missing file, broken JSON, an unlinked dir → exit 0, a log line);
  - `test_capture_is_fast_on_a_huge_transcript` (a 40 MB fixture generated in tmp_path, < 2 s);
  - `test_claim_finish_retry_and_prune`.
- [ ] RED, implement, GREEN, run the full suite, commit `feat(sessions): capture and the session queue`.

### Task 3: Session evidence and the learner's session mode

**Files:** modify `aos/lessons.py`, `aos/mcp_server.py`, `aos/cli.py` (`serve --learn-session ID`), `tests/fake_acp_agent.py`; test `tests/test_session_learner_tools.py`.

**Interfaces:**
- `EVIDENCE_KINDS` gains `"session"`. Session evidence has the shape `{"kind": "session", "tool", "session", "turns": [int], "correction": "correction|refused_tool|self_fix", "at"}`.
- `LessonStore.new`: if every evidence item has kind `session`, confidence starts at 0.4.
- `LearnerTools(aos, board, task=None, max_ops=3, session_entry: dict | None = None)`:
  - in session mode, `learning_input()` returns `{"session": {"tool", "project"}, "corrections": excerpts, "lessons": …, "other_projects": …, "note": QUOTE_NOTE}`;
  - `evidence_events` are excerpt ids;
  - `learning_done(summary)` records the summary on the queue entry (`finish(..., ok=True)`) instead of a board event.
- `aos serve --project P --learn-session ID` serves `build_learner_server` in session mode.
- **Fake agent:** detects `--learn-session` in the aos server args and builds `LearnerTools` in session mode from the queue entry (`FAKE_ACP_LEARN` modes as for board learners).

- [ ] **Tests:**
  - `test_session_only_lesson_starts_below_the_threshold` (0.4, not in `ranked()` at min 0.5);
  - `test_seen_again_reaches_context` (`confirm` with new session evidence → 0.5, ranked);
  - `test_user_confirm_sets_0_8`;
  - `test_mixed_evidence_follows_board_rules`;
  - `test_learning_input_session_mode` (excerpts quoted, lessons listed);
  - `test_evidence_must_be_this_sessions_excerpts`;
  - `test_learning_done_finishes_the_entry`;
  - `test_serve_learn_session_exposes_only_learner_tools`.
- [ ] RED, implement, GREEN, run the full suite, commit `feat(lessons): session evidence and the learner's session mode`.

### Task 4: The background job, commands and doctor

**Files:** create `aos/session_learner.py`; modify `aos/cli.py`; test `tests/test_session_learner.py`.

**Interfaces (produced):**
```python
def learn_entry(aos_home, entry_id, now=None) -> str
    # claim → budget check (sessions/costs.jsonl for today; over → finish(ok=False, "daily budget"))
    # → run the learner: AcpRun.start(spec) with spec built from the entry (context: build_context
    # + LEARNER_PROTOCOL session note; mcp_servers: aos serve --project <path> --learn-session <id>;
    # allowed ["mcp:aos"]; model learning_model), drain loop until turn ends or timeout, every
    # agent request rejected; AdapterMissing → CLI adapter (claude -p / kiro-cli) with the same spec
    # → record cost → finish(ok=learning_done called, reason)
def learn_pending(aos_home, limit: int = 5) -> list[str]
def learn_last(aos_home, project_dir, tool="claude") -> str   # manual trigger: newest transcript of the project
```
- **CLI:**
  - `aos lessons sessions` lists the queue: id, tool, project, status, tries, reason, age.
  - `aos lessons learn-sessions` runs `learn_pending`.
  - `aos lessons learn-session <id>` runs `learn_entry`.
  - `aos lessons learn-session --last [--project-dir DIR]` runs `learn_last`. It finds Claude's newest `~/.claude/projects/<encoded cwd>/*.jsonl` and queues it with all turns as one excerpt window (no hits needed).
- **Doctor:** `line(ok, "session learning: N pending, M failed")`, plus the reasons of failed entries.

- [ ] **Tests** (fake ACP agent; `acp_command` in aos.yaml as in the learning dispatch tests):
  - `test_learn_entry_creates_a_session_lesson` (0.4, `session` evidence, entry `done`);
  - `test_failure_leaves_the_entry_pending_with_a_reason_then_failed_after_three` (`FAKE_ACP_LEARN=crash`);
  - `test_daily_budget_defers_entries`;
  - `test_two_jobs_one_entry` (two `learn_entry` threads → one runs, one returns "taken");
  - `test_learn_last_queues_the_newest_transcript` (a fake `~/.claude/projects` under a tmp HOME);
  - `test_cli_sessions_and_doctor_line`.
- [ ] RED, implement, GREEN, run the full suite, commit `feat(sessions): background learner job, commands and doctor`.

### Task 5: Hooks, docs and the real check

**Files:**
- `aos/adapters/claude.py`: add `w.hook(".claude/settings.local.json", "SessionEnd", ctx.capture_command())`;
- `aos/writer.py`: `LinkContext.capture_command()` → `<aos> lessons capture --tool claude`; the hook passes its stdin JSON;
- `aos/adapters/kiro.py`: no Kiro hook until Task 0's findings;
- `README.md`, `CHANGELOG.md` (0.12.0 unreleased), `ROADMAP.md` (phase B), `skills/aos-feature/SKILL.md` (nothing to change; mention in README only);
- test `tests/test_session_hooks.py`;
- `spikes/session-learning-e2e/README.md`.

- [ ] **Tests:**
  - `test_link_installs_the_session_end_hook`;
  - `test_unlink_removes_it`;
  - `test_relink_does_not_duplicate`.
- [ ] **Docs:** a README "Lessons" paragraph: "Sessions you correct teach it too", with the commands and settings.
- [ ] **Real check** (manual, documented in the spike README), on a demo project linked in a throwaway `AOS_HOME`:
  1. A real Claude Code session where the user corrects a convention ("no, use snake_case for module names").
  2. After it ends, a queue entry, then a session lesson at 0.4, not in `aos context`.
  3. A second session with the same correction raises it to 0.5, and it appears in `aos context`.

  Record the result in the spike README.
- [ ] Commit `feat(sessions): end-of-session hook; docs`.
