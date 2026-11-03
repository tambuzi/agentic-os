# Skill Health and Compliance Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native, as for earlier plans) to implement this plan task-by-task, test-first. Steps use checkbox (`- [ ]`) syntax.

**Goal:**
- Record every skill use from finished sessions and report each skill's health.
- Propose a fix through the inbox for repo skills that keep getting corrected.
- Run hand-written compliance tests for skills headless, with deterministic checks.

**Architecture:**
- `aos/skill_health.py` records uses, computes health and runs the improver. It reuses the session capture, transcripts, excerpts, quoting and the session-learner run machinery.
- `aos/skill_tests.py` parses test files, evaluates checks against tool-call traces, and runs scenarios in a throwaway `AOS_HOME` with headless Claude Code.
- An `AOS_DRY_DISPATCH` switch keeps test runs from launching workers.

**Tech Stack:** Python ≥ 3.11, PyYAML, existing aos modules, pytest, the fake ACP agent, recorded stream-json fixtures.

**Spec:** `docs/superpowers/specs/2026-11-01-skill-health-design.md`

## Global Constraints

- **Skill-use records** go to `<data>/skills/runs.jsonl` as `{skill, tool, project, session, key, outcome, at}`, with no transcript text. An outcome is `corrected` when a correction follows the skill use before the next skill use and within 15 turns.
- **Settings:** `skills.health: true`, `skills.flag_min_corrected: 3`, `skills.flag_rate: 0.4`, `skills.proposal_cooldown_days: 14`. `learning.enabled: false` or `skills.health: false` records nothing.
- **Flagged:** at least 3 corrected uses and a corrected rate of at least 0.4 over 30 days. **Trend:** the 7-day rate against the 30-day rate, with a 10-point margin.
- **The improver** works on repo skills only (`skills/<name>/SKILL.md`), with no open proposal for the skill and no proposal in the last 14 days, and makes one proposal per run.
  - `propose_skill_patch` refusals: `old` not found exactly once, `new` over 2,000 characters, a secret in the text, or `reason` over 500 characters.
  - It uses the learning model and the session-learning daily budget.
- **Test files:** `skills/<name>/tests/<scenario>.yaml`. Files starting with `draft-` never run.
  - **Checks:** `calls` (`at_least`/`at_most`/`distinct`/`where`), `before: [a, b]`, `never`.
  - **Tool names** match with the `mcp__<server>__` prefix stripped.
- **Test runs:** a throwaway `AOS_HOME` with `AOS_DRY_DISPATCH=1`, `claude -p --output-format stream-json --verbose --permission-mode acceptEdits --model <workers.claude.model>`, and `--resume` for later turns.
  - Reports go to `<data>/skills/tests/<skill>-<YYYY-MM-DD-HHMM>.md`, and results to `<data>/skills/tests/results.jsonl`.
  - The exit code is 0 only if every scenario passes.

## Review Focus

1. **Skill-use recording on a huge transcript** (thousands of skill calls) must keep `capture` within its time limit and record at most 50 uses per capture. Pinned by `test_skill_use_recording_is_bounded` (Task 1).
2. **An improver proposal applied after the skill changed** (its `old` text is gone) must fail cleanly on `aos inbox apply` and leave the skill untouched. Pinned by `test_stale_proposal_fails_cleanly` (Task 3).
3. **A test scenario must never launch real workers,** neither through `board_start` (a subprocess) nor `aos board run`. Pinned by `test_dry_dispatch_launches_nothing` (Task 5).
4. **A malformed test file or unknown check** must be reported with its path and must not stop the other scenarios. Pinned by `test_bad_files_are_reported_and_others_run` (Task 4).
5. **The runner's and the improver's own Claude sessions** must never write skill-use records or lessons into the user's real data. Pinned by `test_runner_uses_a_throwaway_home` (Task 5) and the improver's run dir being under `<data>/runs` (Task 3: `test_improver_session_is_ignored_by_capture`).

---

### Task 1: Recording skill uses at the end of a session

**Files:**
- create `aos/skill_health.py` (part 1);
- modify `aos/sessions.py`: `capture` records uses before the corrections check;
- modify `aos/config.py`: `skills.*` defaults;
- test `tests/test_skill_uses.py`.

**Interfaces (produced):**
```python
MAX_USES = 50
def skill_uses(turns: list[Turn], hits: list[Hit]) -> list[dict]
    # Turn.role == "assistant" and Turn.tool == "Skill": skill = json(input)["skill"]
    # outcome "corrected" if a "correction" hit has index in (i, next_skill_use) and <= i + 15
def record_uses(data, tool, project, session, turns, uses) -> int
    # key = sha1(session, turn text/input)[:16]; skips keys already in runs.jsonl for this session
def read_uses(data, days: int | None = None) -> list[dict]
```
- **`sessions.capture`:** after the automated/linked/excluded checks and after reading the transcript, it calls `record_uses` when `skills.health` (default true) and `learning.enabled` are on. Errors are logged and never raised. The correction check then continues as before.

- [ ] **Tests:**
  - `test_skill_use_ok_and_corrected` (a correction 2 turns after → corrected; none → ok);
  - `test_correction_after_the_next_skill_use_counts_for_the_next` (two skill uses, one correction after the second);
  - `test_window_is_15_turns`;
  - `test_uses_are_recorded_once_per_session`;
  - `test_no_records_when_health_or_learning_is_off`;
  - `test_records_hold_no_text`;
  - `test_skill_use_recording_is_bounded` (5,000 skill calls → ≤ 50 records, capture < 2 s).
- [ ] RED, implement, GREEN, run the full suite, commit `feat(skills): record skill uses from finished sessions`.

### Task 2: Health stats, `aos skills health` and doctor

**Files:** `aos/skill_health.py` (part 2), `aos/cli.py`; test `tests/test_skill_health.py`.

**Interfaces (produced):**
```python
def health(data, settings: dict, today: date | None = None) -> list[dict]
    # [{"skill", "uses_7", "uses_30", "rate_7", "rate_30", "trend": "↑"|"↓"|"=", "last_used",
    #   "tests": float | None, "flagged": bool, "repo_skill": bool}]  (sorted: flagged first, then uses)
def flagged(data, settings, repo) -> list[str]
```
- **CLI:** `aos skills health [--days 30]` prints a table. With no data it prints "no skill uses recorded yet".
- **Doctor:** `line(not flagged, "skills: N used, M flagged: a, b")`.

- [ ] **Tests:**
  - `test_stats_and_rates` (fixture records across 40 days);
  - `test_trend_with_margin`;
  - `test_flag_thresholds` (2 corrected → no; 3 of 5 → yes; 3 of 10 → no);
  - `test_repo_skill_vs_plugin`;
  - `test_tests_pass_rate_from_results` (results.jsonl);
  - `test_cli_and_doctor`.
- [ ] RED, implement, GREEN, run the full suite, commit `feat(skills): skill health and aos skills health`.

### Task 3: The skill improver and fix proposals

**Files:**
- `aos/skill_health.py` (part 3);
- `aos/mcp_server.py`: `SkillImproverTools`, `build_improver_server`, `serve --improve-skill NAME`;
- `aos/session_learner.py`: after `learn_pending`, `improve_flagged(aos)`;
- `aos/core.py`: `reject_proposal` records skill-proposal rejections;
- `tests/fake_acp_agent.py`: improver role;
- test `tests/test_skill_improver.py`.

**Interfaces (produced):**
```python
def eligible(aos, name) -> bool             # repo skill, flagged, no open proposal, 14-day cooldown
def improver_input(aos, name) -> dict       # {"skill": name, "text": SKILL.md, "corrections": [excerpts…≤5 sessions]}
def propose(aos, name, old, new, reason) -> str   # validates; inbox.stage(kind="skill", op="patch",
                                                   # args={"name","old","new"}, source="skill-health", reason=…)
def improve(aos, name) -> str               # one AcpRun (session_learner machinery); "proposed <pid>" | "no change" | "failed: …"
def improve_flagged(aos, limit=2) -> list[str]
```
- **Proposal log:** `<data>/skills/proposals.jsonl` (`{skill, proposal, at, status}`) supports the open-proposal and cooldown checks. `AOS.apply_proposal` and `AOS.reject_proposal` mark the status.
- **Excerpts** come from `sessions.excerpts` on the transcripts of recent corrected uses (paths found through the session queue entries, or by `session` id in `~/.claude/projects`). They are quoted and scrubbed.

- [ ] **Tests:**
  - `test_eligibility` (repo skill; plugin skill; open proposal; cooldown);
  - `test_improver_input_is_quoted_and_scrubbed`;
  - `test_propose_validation` (old must appear exactly once; size; secret; one per run);
  - `test_proposal_applies_through_the_skill_writer` (`aos inbox apply` → SKILL.md changed);
  - `test_stale_proposal_fails_cleanly`;
  - `test_reject_starts_the_cooldown`;
  - `test_improve_flagged_with_the_fake_agent` (the fake improver calls `propose_skill_patch`);
  - `test_improver_session_is_ignored_by_capture`.
- [ ] RED, implement, GREEN, run the full suite, commit `feat(skills): propose fixes for skills that keep getting corrected`.

### Task 4: Test files and checks

**Files:** create `aos/skill_tests.py` (part 1); test `tests/test_skill_test_files.py`.

**Interfaces (produced):**
```python
@dataclass
class Scenario: path: Path; skill: str; name: str; tool: str; projects: list[str]; turns: list[str];
                timeout_min: float; checks: list[dict]
class TestFileError(AosError)
def load(path) -> Scenario                     # validates: required keys, types, known check types
def discover(repo, skill: str | None = None, include_drafts=False) -> tuple[list[Scenario], list[str]]   # (ok, errors)
def tool_calls_from_stream(lines: list[str]) -> list[dict]       # [{"name": stripped, "input": {...}}]
def evaluate(scenario, calls) -> list[dict]    # [{"check": str, "ok": bool, "why": str}]
```
- **Evaluation rules** (exact):
  - `calls: t`: n = count of calls named t (with `where` filters) → ok if `at_least ≤ n` (default 1) and `n ≤ at_most`; with `distinct: arg`, n counts distinct values of `input[arg]`.
  - `before: [a, b]`: ok if a was called and `index(first a) < index(first b)`, or b was never called.
  - `never: t`: ok if count is 0.
  - The reason says the numbers, e.g. "task_create called 1×, needed ≥ 2".

- [ ] **Tests:**
  - `test_load_and_validate` (good file; a missing `turns`; unknown check `sometimes`; a bad type);
  - `test_discover_skips_drafts`;
  - `test_bad_files_are_reported_and_others_run`;
  - `test_tool_calls_from_a_recorded_stream` (fixture `tests/fixtures/skill_tests/plan.stream.jsonl`, with prefixes stripped);
  - `test_each_check_pass_and_fail_with_reasons` (parametrised over calls, at_most, distinct, where, before, never).
- [ ] RED, implement, GREEN, run the full suite, commit `feat(skills): compliance test files and deterministic checks`.

### Task 5: The runner, dry dispatch, CLI and CI

**Files:**
- `aos/skill_tests.py` (part 2);
- `aos/mcp_server.py` (`board_start` honours `AOS_DRY_DISPATCH`) and `aos/cli.py` (`board run` honours it too, and `aos skills test`);
- `.github/workflows/tests.yml` (a validate step);
- test `tests/test_skill_test_runner.py`.

**Interfaces (produced):**
```python
def run_scenario(aos, scenario, claude_cmd: list[str] | None = None, keep=False) -> dict
    # {"scenario", "ok", "checks", "calls", "cost_usd", "seconds", "error"}
def run_skill(aos, skill, scenario_name=None, claude_cmd=None, keep=False) -> tuple[bool, Path]   # (all ok, report)
```
- **Throwaway home:** `tempfile.mkdtemp(dir=Path.home())`, with `AOS_HOME` and `AOS_DRY_DISPATCH=1` set in the child environment. It runs `aos init <repo>` and `aos worker claude`, symlinks `~/.agenticos/acp`, and creates each project as `git init`, README, commit and `aos link --no-graphskill`.
- **The turns** run in the first project. The session id comes from the stream's `system/init` or `result` line.
- **Cost** is the `result` line's `total_cost_usd`.
- **Clean-up** in `finally`: the throwaway home and `~/.claude/projects/<folder of each demo project>`.
- **Dry dispatch:** when `AOS_DRY_DISPATCH=1`, `BoardTools.board_start` returns `{"started": false, "dry_run": true, "message": "would start the dispatcher"}` and logs a timeline event. `aos board run` prints the same and exits 0.
- **CLI:** `aos skills test [skill] [scenario] [--keep] [--validate]`. `--validate` is cheap and makes no calls; its exit code is 1 on any error.
- **CI:** a step `python -m aos skills test --validate` after the tests.

- [ ] **Tests** (the `claude_cmd` injected with a small Python script that replays a recorded stream and prints a fake session id):
  - `test_run_scenario_passes_and_writes_the_report_and_results`;
  - `test_failed_check_fails_the_run_with_its_reason`;
  - `test_timeout_fails_the_scenario_and_others_still_run`;
  - `test_dry_dispatch_launches_nothing` (`board_start` in a test home with the env set: no `Popen`; `aos board run`: "would start");
  - `test_runner_uses_a_throwaway_home` (the user's data dir untouched; the throwaway dir removed);
  - `test_cli_validate_exit_codes`.
- [ ] RED, implement, GREEN, run the full suite, commit `feat(skills): aos skills test runner with dry dispatch; CI validates test files`.

### Task 6: Drafts, the first `aos-feature` tests, docs and real checks

**Files:**
- `aos/skill_tests.py` (`draft`), `aos/cli.py` (`--draft`);
- `skills/aos-feature/tests/plan-two-projects.yaml`, `skills/aos-feature/tests/asks-before-creating.yaml`;
- `README.md`, `CHANGELOG.md` (date 0.12.0 as PR #16, then 0.13.0 unreleased), `ROADMAP.md` (item 4);
- test `tests/test_skill_drafts.py`.

- [ ] **`draft(aos, skill)`:** a cheap run (ACP with the learning model, CLI fallback) given SKILL.md and the check vocabulary, with one tool `write_test(yaml_text)`. The text is validated with `load()` before `skills/<name>/tests/draft-<slug>.yaml` is written, and the path is returned.
- [ ] **Tests:**
  - `test_draft_writes_a_valid_draft_file` (the fake agent calls `write_test`);
  - `test_invalid_draft_is_refused`;
  - `test_shipped_aos_feature_tests_validate` (the two files load cleanly).
- [ ] **Docs:**
  - README section 4: "Skills: health and tests", covering `aos skills health`, fix proposals, `aos skills test`, `--draft` and the settings;
  - CHANGELOG;
  - ROADMAP: tick "Skill runs and health" and "Skill compliance tests".
- [ ] **Real checks:**
  1. `aos skills test aos-feature` with real Claude Code; record the report in `spikes/skill-health-e2e/REPORT-<date>.md`.
  2. A health run on real sessions in a throwaway home, created headless as in the session-learning spike: a skill corrected 3 times out of 5 is flagged, and a proposal lands in the inbox.
- [ ] Commit `feat(skills): drafts, aos-feature compliance tests, docs` and `spike: skill health and tests end to end`.
