# Skill health and skill compliance tests (ROADMAP item 4)

Status: design approved in conversation 2026-11-01 (sections 1–2); awaiting written-spec review.

## 1. Goal

Know how the skills agents use are doing, and make sure agents actually follow them.

- **Health from real use** (built first): which skills get used, how often, and how often the user had to correct the agent afterwards. A skill that keeps getting corrected gets a concrete fix proposed through the inbox.
- **Compliance tests** (built second): scenario files next to each skill, run headless on demand. Each one checks the agent's tool calls against the skill's expected steps with fixed rules.

The decisions: both, health first (C). A struggling skill gets a reported and *proposed* fix (B). Test files are hand-written, with deterministic checks and AI-drafted files you review (C).

## 2. Skill health

### 2.1 Recording skill uses

The end-of-session capture (`aos lessons capture`, spec 2026-10-30) already reads the transcript. It now also records every skill use, with no AI and no cost, for every session (not only sessions with corrections).
- **A skill use** is a Claude Code `Skill` tool call (`input.skill`, e.g. `aos-feature`, `superpowers:writing-plans`).
- **Its outcome** is `corrected` if one of the user's corrections (the session-learning correction rules) comes after it, before the next skill use and within 15 turns. Otherwise it is `ok`.
- **Records** go to `<data>/skills/runs.jsonl`: `{skill, tool, project, session, key, outcome, at}`. The `key` (hash of session and turn text) deduplicates repeated captures of the same session. No transcript text is stored.
- **Not recorded:** automated sessions (board worktrees, `<data>/runs`), excluded projects, and anything when `skills.health: false` or `learning.enabled: false`.

### 2.2 `aos skills health [--days 30]`

For each skill seen, it shows:
- uses over 7 and 30 days;
- the corrected rate over 7 and 30 days;
- a trend (↑ better, ↓ worse, = steady), from the 7-day rate against the 30-day rate with a 10-point margin;
- when it was last used;
- the last compliance-test pass rate, if any;
- whether it is flagged.

**Flagged:** at least `skills.flag_min_corrected` (3) corrected uses, and a corrected rate of at least `skills.flag_rate` (0.4) over 30 days. `aos doctor` reports flagged skills.

### 2.3 Fix proposals

A flagged skill gets one inbox proposal when all of these hold:
- the skill lives in this repo's `skills/` (plugin skills such as `superpowers:*` are reported only);
- it has no open proposal;
- no proposal was made for it in the last `skills.proposal_cooldown_days` (14) days.

**The skill improver** is a short read-only AI run, launched by the same background job that runs after a session ends.
- It uses the project tool's `learning_model`, through the session learner machinery (AcpRun, CLI fallback, the session learning daily budget).
- It is served by `aos serve --improve-skill <name>`, which exposes two tools only:
  - **`skill_input()`** returns the skill's current text and quoted, scrubbed excerpts of the corrections that followed its recent corrected uses: up to 5 sessions, 3 turns before and 1 after each correction. It re-reads the transcripts, which are still on disk.
  - **`propose_skill_patch(old, new, reason)`** proposes one focused edit. `old` must occur exactly once in the skill. `new` is at most 2,000 characters, with no secrets (lessons secret check). `reason` is at most 500 characters. Only one proposal is allowed per run.
- **The result** is an inbox proposal of kind `skill`, op `patch`, staged with the existing `inbox.stage`. Its reason lists the evidence: the corrected rate, the sessions and the improver's reason.
- `aos inbox apply` applies it through the existing skill writer; it shows up in `git diff`, as skills always have. `aos inbox reject` records the rejection, and the cooldown applies.
- A run that ends without a proposal is fine: "nothing to change" is valid.

### 2.4 Settings

```yaml
skills:
  health: true
  flag_min_corrected: 3
  flag_rate: 0.4
  proposal_cooldown_days: 14
```

## 3. Compliance tests

### 3.1 Test files

Test files live in the repo at `skills/<name>/tests/<scenario>.yaml` and are reviewed like code.

```yaml
scenario: plan-two-projects
tool: claude                       # claude | any (any runs with Claude until Kiro tests exist)
projects: [api, web]               # throwaway demo projects, linked in a throwaway aos home
turns:                             # what "the user" says, in order
  - "Deliver a feature: add a currency field to orders in api and show it in web."
  - "The plan looks good, approved. Go ahead."
timeout_min: 6
checks:
  - calls: code_query
    at_least: 1
  - before: [code_query, task_create]
  - calls: task_create
    at_least: 2
    distinct: project
  - calls: board_start
    at_least: 1
  - never: task_complete
```

**Check vocabulary** (fixed, and validated when the file is read):
- **`calls: <tool>`** with any of:
  - `at_least: n` / `at_most: n` (default `at_least: 1`);
  - `distinct: <arg>`: at least `at_least` different values of that argument among the calls;
  - `where: {arg: value}`: only calls whose argument equals the value count.
- **`before: [a, b]`:** the first call of `a` comes before the first call of `b`, and `a` was called at all.
- **`never: <tool>`:** no call of `tool`.

Tool names are matched without their MCP prefix: `mcp__aos__task_create` matches `task_create`, and plain Claude tools like `Bash` match as they are.

### 3.2 Running: `aos skills test <skill> [scenario] [--draft] [--validate]`

Each scenario runs in a throwaway `AOS_HOME`:
1. **Setup:** the throwaway home is initialised from this repo. `aos worker claude` is set, and the Claude ACP adapter is symlinked when installed. Each project in `projects` is created as a git repository with a README and linked with `aos link --no-graphskill`.
2. **Headless turns:** the first turn runs `claude -p --output-format stream-json --verbose --permission-mode acceptEdits --model <workers.claude.model>` in the first project. Each further turn adds `--resume <session id>`.
3. **No real workers:** the environment sets `AOS_DRY_DISPATCH=1`. With it, `board_start` and `aos board run` record "would start the dispatcher" on the timeline and return success without launching workers.
4. **Evaluation:** the tool calls (`tool_use` blocks with name and input) are collected from the stream, and each check is evaluated. A scenario that times out fails with "timed out".
5. **Report:** each check passes or fails with a reason, for example "task_create called 1×, needed ≥ 2". It includes the tool-call timeline, cost and duration, and is written to `<data>/skills/tests/<skill>-<YYYY-MM-DD-HHMM>.md`. The pass rate is stored in `<data>/skills/tests/results.jsonl` for `aos skills health`.
6. **Clean-up:** the throwaway home and the demo projects' transcripts in `~/.claude/projects` are deleted, unless `--keep`.

**Exit code:** 0 when every scenario passes, 1 otherwise.

**`--draft`:** a cheap AI run reads the skill and writes `skills/<name>/tests/draft-<slug>.yaml` with suggested turns and checks. It goes through the same validation. A file starting with `draft-` is never run until the user renames it.

**`--validate`:** parses every test file and checks the schema, at no cost. CI runs it on every pull request.

### 3.3 First tests shipped

`skills/aos-feature/tests/`:
- **`plan-two-projects.yaml`** (above): separate tasks per project, `code_query` before creating tasks, it starts the board, and it never does a task itself.
- **`asks-before-creating.yaml`:** with only the first turn and no approval, there is no `task_create` and no `feature_create` (the skill says "Do not create anything before approval").

## 4. Failure handling

- **Health recording** happens inside `capture`, under the same 2-second, never-fail rules. A broken record is logged and skipped.
- **A failing improver run** follows the session-learning queue rules: its reason is recorded, and it is retried by later jobs, up to 3 times.
- **Test runs:**
  - a missing `claude` CLI is a clear error;
  - a scenario that crashes or times out is a failed scenario with its reason, and the other scenarios still run;
  - clean-up runs in `finally`.
- **A malformed test file** is reported with its path and the error, and that file doesn't run.

## 5. Testing

**Unit tests:**
- **Skill uses and outcomes** from transcript fixtures: `ok` and `corrected` within the window; a correction after the next skill use is not counted; deduplication across captures; automated sessions and settings respected.
- **Health:** the stats, the trend and the flag thresholds.
- **Improver eligibility:** repo skill only; open proposal; cooldown.
- **Improver tools:** `skill_input` is quoted and scrubbed; `propose_skill_patch` validation (old must occur once, size, secrets, one per run); the inbox proposal applies through the skill writer.
- **Test files:** parsing and validation errors; every check type, against recorded tool-call traces (pass and fail with the right reason); prefix-stripped tool names; `draft-` files skipped.
- **`AOS_DRY_DISPATCH`:** `board_start` records "would start" and launches nothing.

**Integration tests** (fake agent and recorded streams):
- the improver job stages a proposal;
- `aos skills test` runs against a fake `claude` that replays a recorded stream-json transcript (injected command), and writes the report and the results row;
- `aos skills health` and the doctor line;
- `--validate` in CI.

**Real check:**
- `aos skills test aos-feature` with real Claude Code: both scenarios pass, or the report shows exactly which check failed.
- A skill-health run on real sessions in a throwaway home: a skill corrected 3 times out of 5 is flagged, and a proposal lands in the inbox.

## 6. Not in this spec

- Kiro test runs (the stream format is still to be checked) and Kiro skill-use recording (it needs the Kiro session capture spike).
- The skill stock-take verdicts (keep, improve, update, retire, merge): a later periodic review.
- Board workers' skill use.
- AI-judged checks.
