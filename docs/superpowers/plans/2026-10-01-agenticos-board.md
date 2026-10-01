# agenticOS Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local task board with a dispatcher that runs multi-project features as parallel headless workers on Claude Code or Kiro. Each worker gets the shared feature context plus its own project's context.

**Architecture:**
- `board.py` owns a SQLite file (`<data>/board.db`) and every state transition.
- `worktrees.py` isolates work in git worktrees.
- `workers/` builds the shared inputs once, then a small adapter per tool (`claude`, `kiro`, `command`) turns them into a process launch.
- `dispatcher.py` loops: reap, promote, pick, launch.
- The MCP server gains board tools (worker mode with `--task`, planner mode without).
- The CLI gains `feature`, `task` and `board` commands.

**Tech Stack:** Python ≥3.11, stdlib `sqlite3` / `subprocess` / `fcntl` / `signal`, `pyyaml`, `mcp`, `pytest`. Git ≥2.31 (`rev-parse --path-format`).

**Spec:** `docs/superpowers/specs/2026-10-01-agenticos-board-design.md` (core spec: `docs/superpowers/specs/2026-10-01-agenticos-design.md`).

## Global Constraints

- `board.db` is a plain SQLite file in the local data dir (`AOS(...).data`). There is no server. Use WAL, `busy_timeout=5000`, and `BEGIN IMMEDIATE` for writes.
- Business data stays local: board, feature files, logs and run dirs live under the data dir. Worktrees live under `$AOS_HOME/worktrees/<feature>/<project>` on branch `feature/<feature>`.
- The user's own checkout is never modified. Workers never push.
- Neither adapter may pass `bypassPermissions`, `--dangerously-skip-permissions` or `--trust-all-tools`.
- Allowed tools use the neutral forms `read`, `write`, `shell:<prefix>` and `mcp:<server>`, translated per adapter.
- Process args are lists, never run through a shell.
- Defaults: `board.parallel: 3`, `board.max_tasks_per_feature: 30`, `board.default_worker: claude`, `workers.common.timeout_min: 45`, `workers.common.max_attempts: 2`.
- Every state change writes an `events` row. MCP tools never raise (`_safe`).
- **Plan-time ruling:** the Claude worker passes context with `--append-system-prompt <text>`. `claude --help` (2.1.286) lists no standalone `--append-system-prompt-file` flag.
- **Plan-time ruling:** dependencies can only be set when a task is created, and must name existing tasks. Cycles are therefore impossible by construction, so no cycle detector is needed. This differs from spec §4's "rejected when added", with the same effect.
- **Plan-time ruling:** the Kiro shell allow-list uses the legacy `toolsSettings.shell.allowedCommands` regex list. The `permissions` rule syntax is not documented enough to target. It will be checked on a live `kiro-cli` (user check, 2026-10-02). v1 records no Kiro session id; resume uses `--resume` (last session in the worktree).

## Review Focus

1. A worker that crashes, exits 0 without `task_complete`, or hangs past its timeout must end as `ready` (retry) or `failed`, never stuck in `running`. (Task 5: `test_failures_retry_then_failed`, `test_exit_zero_without_complete`, `test_timeout_kills_worker`)
2. A task whose dependency failed or was cancelled must show as stuck, not wait silently forever. (Task 1: `test_stuck_on_failed_dependency`; Task 7: `aos board` prints it)
3. After a contract change, a worker cannot complete until it has re-read the contract. (Task 2: `test_claim_complete_and_contract_guard`; Task 6: `test_worker_complete_refused_until_seen`)
4. Two tasks of the same feature and project must never run at once, because they share one worktree. (Task 5: `test_parallel_cap_and_one_per_worktree`)
5. The user's checkout and branch must stay untouched while workers commit in worktrees, and an existing branch must not be reset. (Task 3: `test_creates_and_reuses`, `test_recreate_keeps_branch_commits`; Task 5: `test_worker_writes_only_in_worktree`)

---

## File Structure

```
aos/config.py            + board/workers DEFAULTS
aos/board.py             SQLite schema + all transitions, events, proposals, contract files
aos/worktrees.py         ensure/reuse/validate git worktrees
aos/workers/__init__.py  adapter registry
aos/workers/common.py    profile resolution, settings, worker context, MCP servers, RunSpec/Launch, prepare_run
aos/workers/claude.py    Claude Code adapter
aos/workers/kiro.py      Kiro adapter
aos/workers/command.py   generic argv-template adapter
aos/dispatcher.py        Dispatcher loop, lock, recovery, signals
aos/mcp_server.py        + BoardTools and registration (worker / planner mode), run_server(task=)
aos/cli.py               + feature / task / board commands, doctor checks
aos.yaml                 + board / workers sections
skills/aos-plan-feature/SKILL.md
README.md                + "Multi-project features" section
tests/conftest.py        + git_repo fixture
tests/fake_worker.py     fake worker process for dispatcher tests
tests/test_board.py tests/test_worktrees.py tests/test_workers.py tests/test_dispatcher.py
tests/test_mcp_board.py tests/test_cli_board.py
```

---

### Task 1: Settings and board core (features, tasks, events, promotion)

**Files:**
- Modify: `aos/config.py` (DEFAULTS), `aos.yaml`, `tests/conftest.py`
- Create: `aos/board.py`
- Test: `tests/test_board.py`

**Interfaces:**
- Consumes: `check_slug`, `AosError`, `read_text`, `write_atomic`
- Produces:
  - Constants: `Board(data: Path, max_tasks_per_feature: int = 30)`, `STATUSES`, `now() -> str`.
  - Features: `.create_feature(slug, title, brief="", contract="", author="human") -> dict`, `.feature(slug) -> dict`, `.features(status=None) -> list[dict]`, `.set_feature_status(slug, status, author="human")`, `.feature_dir(slug) -> Path`, `.brief(slug) -> str`, `.contract(slug) -> {"version": int, "text": str}`.
  - Tasks: `.add_task(feature, project, title, spec="", depends_on=(), worker="claude", max_attempts=2, created_by="human") -> int`, `.task(id) -> dict` (adds `depends_on: list[int]`), `.tasks(feature=None, status=None) -> list[dict]`, `.dependency_results(id) -> list[dict]`, `.promote() -> list[int]`, `.stuck() -> dict[int, list[int]]`.
  - Events: `.events(feature, since=0, limit=None) -> list[dict]`, `.comment(id, text, author)`.
  - Internal (used by Task 2): `_tx()`, `_rows()`, `_event(c, ...)`, `_transition(c, id, allowed_from, to, author, body, **fields)`.
  - Fixture `git_repo(path) -> Path` creates a repo on `main` with one commit.

- [ ] **Step 1: Write the failing test**

Append to `tests/conftest.py`:
```python
@pytest.fixture
def git_repo(monkeypatch):
    """Factory: a git repo on branch main with one commit."""
    import subprocess
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)

    def _make(path: Path) -> Path:
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
        (path / "README.md").write_text("hi\n")
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
        return path

    return _make
```

`tests/test_board.py`:
```python
import pytest

from aos.board import Board
from aos.errors import AosError


@pytest.fixture
def board(data):
    return Board(data)


def feat(board, slug="checkout"):
    board.create_feature(slug, "Checkout v2", brief="# Goal\nNew checkout.\n", contract="POST /orders")
    return slug


def test_create_feature_writes_files_and_event(board, data):
    feat(board)
    f = board.feature("checkout")
    assert f["status"] == "open" and f["contract_version"] == 1
    assert (data / "features/checkout/brief.md").read_text().startswith("# Goal")
    assert board.contract("checkout") == {"version": 1, "text": "POST /orders\n"}
    assert board.events("checkout")[0]["kind"] == "status"
    with pytest.raises(AosError):
        feat(board)
    with pytest.raises(AosError):
        board.create_feature("Bad Slug", "x")
    with pytest.raises(AosError):
        board.feature("nope")


def test_tasks_and_promotion(board):
    feat(board)
    a = board.add_task("checkout", "shop-api", "Add orders endpoint")
    b = board.add_task("checkout", "web", "Call orders endpoint", depends_on=[a])
    assert board.promote() == [a]
    assert board.task(b)["status"] == "todo" and board.task(b)["depends_on"] == [a]
    with board._tx() as c:
        board._transition(c, a, ("ready",), "done", "test", "", result="endpoint live")
    assert board.promote() == [b]
    assert board.dependency_results(b)[0]["result"] == "endpoint live"
    assert [t["id"] for t in board.tasks(feature="checkout", status="ready")] == [b]


def test_add_task_validation(board):
    feat(board)
    feat(board, "other")
    x = board.add_task("other", "web", "x")
    with pytest.raises(AosError):
        board.add_task("checkout", "web", "y", depends_on=[x])
    with pytest.raises(AosError):
        board.add_task("checkout", "web", "y", depends_on=[999])
    with pytest.raises(AosError):
        board.add_task("nope", "web", "y")
    with pytest.raises(AosError):
        board.add_task("checkout", "Bad Project", "y")
    with pytest.raises(AosError):
        board.add_task("checkout", "web", "  ")
    small = Board(board.data, max_tasks_per_feature=1)
    small.add_task("checkout", "web", "1")
    with pytest.raises(AosError) as e:
        small.add_task("checkout", "web", "2")
    assert "max" in e.value.message
    board.set_feature_status("checkout", "done")
    with pytest.raises(AosError):
        board.add_task("checkout", "web", "z")


def test_stuck_on_failed_dependency(board):
    feat(board)
    a = board.add_task("checkout", "api", "a")
    b = board.add_task("checkout", "web", "b", depends_on=[a])
    board.promote()
    with board._tx() as c:
        board._transition(c, a, ("ready",), "failed", "test", "")
    assert board.promote() == []
    assert board.stuck() == {b: [a]}


def test_events_since_and_limit(board):
    feat(board)
    t = board.add_task("checkout", "web", "x")
    for i in range(5):
        board.comment(t, f"c{i}", author="w")
    evs = board.events("checkout")
    last = evs[-3]["id"]
    assert [e["body"] for e in board.events("checkout", since=last)] == ["c3", "c4"]
    assert [e["body"] for e in board.events("checkout", limit=2)] == ["c3", "c4"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_board.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aos.board'`

- [ ] **Step 3: Implement**

In `aos/config.py`, extend `DEFAULTS` (add after `"context": {...}`):
```python
    "board": {"parallel": 3, "max_tasks_per_feature": 30, "default_worker": "claude"},
    "workers": {
        "common": {
            "timeout_min": 45,
            "max_attempts": 2,
            "allowed_tools": ["read", "write", "shell:git status", "shell:git diff", "shell:git log",
                              "shell:git add", "shell:git commit", "mcp:aos", "mcp:graphskill"],
        },
        "claude": {"model": "sonnet"},
        "kiro": {"model": None},
    },
```

Append to `aos.yaml`:
```yaml
board:
  parallel: 3                 # workers running at the same time
  max_tasks_per_feature: 30
  default_worker: claude      # claude | kiro (per project: projects.yaml worker.profile)
workers:
  common:
    timeout_min: 45
    max_attempts: 2
    allowed_tools: [read, write, "shell:git status", "shell:git diff", "shell:git log",
                    "shell:git add", "shell:git commit", "mcp:aos", "mcp:graphskill"]
  claude: {model: sonnet}
  kiro:   {model: null}       # null = Kiro's default model
```

`aos/board.py`:
```python
"""The feature/task board: one SQLite file (<data>/board.db). No server.

Every state change happens here inside a write transaction and is recorded as
an event, so the CLI, the MCP tools and the dispatcher always agree.
Dependencies are fixed at task creation and must name existing tasks, so
cycles cannot be formed.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import check_slug
from .errors import AosError
from .store import read_text, write_atomic

STATUSES = ("todo", "ready", "running", "blocked", "done", "failed", "cancelled")
FEATURE_STATUSES = ("open", "done", "cancelled")
CONTRACT_HEADER = "<!-- aos contract v{} -->"

SCHEMA = """
CREATE TABLE IF NOT EXISTS features(
  slug TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
  contract_version INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, feature TEXT NOT NULL, project TEXT NOT NULL,
  title TEXT NOT NULL, spec TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'todo',
  worker TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
  contract_seen INTEGER, result TEXT, session_id TEXT, pid INTEGER, note TEXT,
  resume INTEGER NOT NULL DEFAULT 0, started TEXT, updated TEXT NOT NULL, created_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deps(task INTEGER NOT NULL, depends_on INTEGER NOT NULL,
  PRIMARY KEY(task, depends_on));
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, feature TEXT NOT NULL, task INTEGER, ts TEXT NOT NULL,
  kind TEXT NOT NULL, author TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, feature TEXT NOT NULL, task INTEGER, body TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', decided TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _contract_file(version: int, body: str) -> str:
    head = CONTRACT_HEADER.format(version)
    return f"{head}\n{body.strip()}\n" if body.strip() else f"{head}\n"


def _contract_body(text: str) -> str:
    if text.startswith("<!-- aos contract"):
        return text.split("\n", 1)[1] if "\n" in text else ""
    return text


class Board:
    def __init__(self, data: str | Path, max_tasks_per_feature: int = 30):
        self.data = Path(data)
        self.path = self.data / "board.db"
        self.max_tasks = int(max_tasks_per_feature)

    # -- plumbing ------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        self.data.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        return c

    @contextmanager
    def _tx(self):
        with closing(self._connect()) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")

    def _rows(self, sql: str, params=()) -> list[dict]:
        with closing(self._connect()) as c:
            return [dict(r) for r in c.execute(sql, params)]

    @staticmethod
    def _event(c, feature: str, task: int | None, kind: str, author: str, body: str) -> None:
        c.execute("INSERT INTO events(feature, task, ts, kind, author, body) VALUES (?,?,?,?,?,?)",
                  (feature, task, now(), kind, author, body))

    @staticmethod
    def _task_row(c, tid: int):
        t = c.execute("SELECT * FROM tasks WHERE id=?", (int(tid),)).fetchone()
        if not t:
            raise AosError(f"unknown task #{tid}", "aos task list")
        return t

    def _transition(self, c, tid: int, allowed_from: tuple, to: str, author: str, body: str,
                    **fields) -> dict:
        t = self._task_row(c, tid)
        if t["status"] not in allowed_from:
            raise AosError(f"task #{tid} is {t['status']}; cannot move to {to}")
        sets = {"status": to, "updated": now(), **fields}
        c.execute(f"UPDATE tasks SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                  (*sets.values(), int(tid)))
        self._event(c, t["feature"], int(tid), "status", author, body or f"{t['status']} → {to}")
        return dict(t)

    # -- features ------------------------------------------------------------
    def feature_dir(self, slug: str) -> Path:
        check_slug(slug)
        return self.data / "features" / slug

    def create_feature(self, slug: str, title: str, brief: str = "", contract: str = "",
                       author: str = "human") -> dict:
        check_slug(slug)
        if not (title or "").strip():
            raise AosError("feature needs a title")
        with self._tx() as c:
            if c.execute("SELECT 1 FROM features WHERE slug=?", (slug,)).fetchone():
                raise AosError(f"feature {slug!r} already exists")
            c.execute("INSERT INTO features(slug, title, status, contract_version, created) "
                      "VALUES (?,?,?,?,?)", (slug, title.strip(), "open", 1, now()))
            self._event(c, slug, None, "status", author, f"feature created: {title.strip()}")
            d = self.feature_dir(slug)
            write_atomic(d / "brief.md", brief)
            write_atomic(d / "contract.md", _contract_file(1, contract))
        return self.feature(slug)

    def feature(self, slug: str) -> dict:
        rows = self._rows("SELECT * FROM features WHERE slug=?", (slug,))
        if not rows:
            raise AosError(f"unknown feature {slug!r}", "aos feature list")
        return rows[0]

    def features(self, status: str | None = None) -> list[dict]:
        if status:
            return self._rows("SELECT * FROM features WHERE status=? ORDER BY created", (status,))
        return self._rows("SELECT * FROM features ORDER BY created")

    def set_feature_status(self, slug: str, status: str, author: str = "human") -> None:
        if status not in FEATURE_STATUSES:
            raise AosError(f"unknown feature status {status!r}", ", ".join(FEATURE_STATUSES))
        self.feature(slug)
        with self._tx() as c:
            c.execute("UPDATE features SET status=? WHERE slug=?", (status, slug))
            self._event(c, slug, None, "status", author, f"feature {status}")

    def brief(self, slug: str) -> str:
        self.feature(slug)
        return read_text(self.feature_dir(slug) / "brief.md")

    def contract(self, slug: str) -> dict:
        f = self.feature(slug)
        return {"version": f["contract_version"],
                "text": _contract_body(read_text(self.feature_dir(slug) / "contract.md"))}

    # -- tasks ---------------------------------------------------------------
    def add_task(self, feature: str, project: str, title: str, spec: str = "", depends_on=(),
                 worker: str = "claude", max_attempts: int = 2, created_by: str = "human") -> int:
        check_slug(project)
        if not (title or "").strip():
            raise AosError("task needs a title")
        deps = sorted({int(d) for d in (depends_on or [])})
        with self._tx() as c:
            f = c.execute("SELECT status FROM features WHERE slug=?", (feature,)).fetchone()
            if not f:
                raise AosError(f"unknown feature {feature!r}", "aos feature list")
            if f["status"] != "open":
                raise AosError(f"feature {feature!r} is {f['status']}")
            n = c.execute("SELECT COUNT(*) FROM tasks WHERE feature=?", (feature,)).fetchone()[0]
            if n >= self.max_tasks:
                raise AosError(f"feature {feature!r} already has {n} tasks (max {self.max_tasks})",
                               "raise board.max_tasks_per_feature in aos.yaml")
            for d in deps:
                r = c.execute("SELECT feature FROM tasks WHERE id=?", (d,)).fetchone()
                if not r or r["feature"] != feature:
                    raise AosError(f"dependency #{d} is not a task of feature {feature!r}")
            cur = c.execute(
                "INSERT INTO tasks(feature, project, title, spec, status, worker, max_attempts, "
                "updated, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                (feature, project, title.strip(), spec or "", "todo", worker, int(max_attempts),
                 now(), created_by))
            tid = cur.lastrowid
            c.executemany("INSERT INTO deps(task, depends_on) VALUES (?,?)", [(tid, d) for d in deps])
            self._event(c, feature, tid, "status", created_by,
                        f"task #{tid} created for {project}: {title.strip()}")
        return tid

    def task(self, tid: int) -> dict:
        rows = self._rows("SELECT * FROM tasks WHERE id=?", (int(tid),))
        if not rows:
            raise AosError(f"unknown task #{tid}", "aos task list")
        t = rows[0]
        t["depends_on"] = [r["depends_on"] for r in
                           self._rows("SELECT depends_on FROM deps WHERE task=? ORDER BY depends_on",
                                      (t["id"],))]
        return t

    def tasks(self, feature: str | None = None, status: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM tasks WHERE 1=1", []
        if feature:
            sql, params = sql + " AND feature=?", params + [feature]
        if status:
            sql, params = sql + " AND status=?", params + [status]
        return self._rows(sql + " ORDER BY id", params)

    def dependency_results(self, tid: int) -> list[dict]:
        return self._rows("SELECT t.id, t.project, t.title, t.status, t.result FROM deps d "
                          "JOIN tasks t ON t.id=d.depends_on WHERE d.task=? ORDER BY t.id", (int(tid),))

    def promote(self) -> list[int]:
        with self._tx() as c:
            ids = [r[0] for r in c.execute(
                "SELECT t.id FROM tasks t WHERE t.status='todo' AND NOT EXISTS ("
                " SELECT 1 FROM deps d JOIN tasks x ON x.id=d.depends_on"
                " WHERE d.task=t.id AND x.status!='done') ORDER BY t.id")]
            for tid in ids:
                self._transition(c, tid, ("todo",), "ready", "dispatcher", "dependencies done → ready")
        return ids

    def stuck(self) -> dict[int, list[int]]:
        """todo tasks that can never become ready because a dependency failed or was cancelled."""
        out: dict[int, list[int]] = {}
        for r in self._rows("SELECT d.task, d.depends_on FROM deps d JOIN tasks t ON t.id=d.task "
                            "JOIN tasks x ON x.id=d.depends_on WHERE t.status='todo' "
                            "AND x.status IN ('failed','cancelled') ORDER BY d.task, d.depends_on"):
            out.setdefault(r["task"], []).append(r["depends_on"])
        return out

    # -- events --------------------------------------------------------------
    def events(self, feature: str, since: int = 0, limit: int | None = None) -> list[dict]:
        if limit:
            return self._rows("SELECT * FROM (SELECT * FROM events WHERE feature=? AND id>? "
                              "ORDER BY id DESC LIMIT ?) ORDER BY id", (feature, int(since), int(limit)))
        return self._rows("SELECT * FROM events WHERE feature=? AND id>? ORDER BY id",
                          (feature, int(since)))

    def comment(self, tid: int, text: str, author: str) -> None:
        if not (text or "").strip():
            raise AosError("comment is empty")
        with self._tx() as c:
            t = self._task_row(c, tid)
            self._event(c, t["feature"], int(tid), "comment", author, text.strip())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_board.py tests/test_seed.py tests/test_config.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add aos/config.py aos.yaml aos/board.py tests/conftest.py tests/test_board.py
git commit -m "feat(board): SQLite board core: features, tasks, events, promotion"
```

---

### Task 2: Board transitions and contract proposals

**Files:**
- Modify: `aos/board.py`
- Test: `tests/test_board.py` (append)

**Interfaces:**
- Consumes: Task 1 internals (`_tx`, `_rows`, `_event`, `_task_row`, `_transition`, `_contract_file`, `_contract_body`)
- Produces:
  - Task transitions: `.claim(id) -> dict` (ready→running, attempts+1, contract_seen=current), `.set_process(id, pid, session_id)`, `.mark_seen(id)`, `.complete(id, summary, author)`, `.block(id, reason, author)`, `.unblock(id, note="", author="human")`, `.attempt_failed(id, reason)`, `.retry(id, note=None, worker=None, resume=False, author="human")`, `.cancel(id, author="human")`.
  - Proposals: `.propose(id, body, reason, author) -> int`, `.proposals(feature=None, status=None) -> list[dict]`, `.approve(proposal_id, new_contract=None, author="human") -> {"proposal", "contract_version"}`, `.reject(proposal_id, reason="", author="human")`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_board.py`:
```python
def test_claim_complete_and_contract_guard(board):
    feat(board)
    t = board.add_task("checkout", "web", "x")
    board.promote()
    assert board.claim(t)["attempts"] == 1
    p = board.propose(t, "POST /orders returns 201", "need status code", author=f"task:{t}")
    board.approve(p)
    with pytest.raises(AosError) as e:
        board.complete(t, "done", author="w")
    assert "v1→v2" in e.value.message
    board.mark_seen(t)
    board.complete(t, "done", author="w")
    assert board.task(t)["status"] == "done" and board.task(t)["result"] == "done"
    assert board.events("checkout")[-1]["kind"] == "result"
    with pytest.raises(AosError):
        board.claim(t)


def test_approve_versions_contract_files(board, data):
    feat(board)
    t = board.add_task("checkout", "web", "x")
    p = board.propose(t, "Add field currency", "multi-currency", author="w")
    assert board.proposals("checkout", "pending")[0]["id"] == p
    assert board.approve(p) == {"proposal": p, "contract_version": 2}
    c = board.contract("checkout")
    assert c["version"] == 2 and "POST /orders" in c["text"] and "Add field currency" in c["text"]
    assert (data / "features/checkout/contract.v1.md").read_text() == "<!-- aos contract v1 -->\nPOST /orders\n"
    with pytest.raises(AosError):
        board.approve(p)
    p2 = board.propose(t, "Drop field", "nah", author="w")
    board.reject(p2, "keep it")
    assert board.proposals("checkout", "rejected")[0]["id"] == p2
    p3 = board.propose(t, "x", "y", author="w")
    assert board.approve(p3, new_contract="FULL REWRITE")["contract_version"] == 3
    assert board.contract("checkout")["text"] == "FULL REWRITE\n"
    kinds = [e["kind"] for e in board.events("checkout") if e["kind"] in ("proposal", "contract", "decision")]
    assert kinds == ["proposal", "contract", "proposal", "decision", "proposal", "contract"]


def test_failed_attempts_retry_block_cancel(board):
    feat(board)
    t = board.add_task("checkout", "web", "x", max_attempts=2)
    board.promote()
    board.claim(t)
    board.set_process(t, 4242, "sess-1")
    assert board.task(t)["pid"] == 4242 and board.task(t)["session_id"] == "sess-1"
    board.attempt_failed(t, "crash")
    assert board.task(t)["status"] == "ready" and board.task(t)["pid"] is None
    board.claim(t)
    board.attempt_failed(t, "crash again")
    assert board.task(t)["status"] == "failed"
    board.retry(t, note="try smaller steps")
    task = board.task(t)
    assert task["status"] == "ready" and task["attempts"] == 0 and task["note"] == "try smaller steps"
    board.claim(t)
    board.block(t, "needs API key", author="w")
    assert board.task(t)["status"] == "blocked"
    assert board.events("checkout")[-1]["kind"] == "blocker"
    board.unblock(t, note="key is in vault")
    assert board.task(t)["status"] == "ready" and board.task(t)["note"] == "key is in vault"
    board.cancel(t)
    assert board.task(t)["status"] == "cancelled"
    with pytest.raises(AosError):
        board.retry(t, resume=True, worker="kiro")
    board.retry(t, resume=True)
    assert board.task(t)["resume"] == 1
    with pytest.raises(AosError):
        board.attempt_failed(t, "not running")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_board.py -q`
Expected: FAIL with `AttributeError: 'Board' object has no attribute 'claim'`

- [ ] **Step 3: Implement**

Append to class `Board` in `aos/board.py`:
```python
    # -- task transitions ----------------------------------------------------
    def _version(self, c, feature: str) -> int:
        return c.execute("SELECT contract_version FROM features WHERE slug=?", (feature,)).fetchone()[0]

    def claim(self, tid: int) -> dict:
        with self._tx() as c:
            t = self._task_row(c, tid)
            self._transition(c, tid, ("ready",), "running", "dispatcher",
                             f"attempt {t['attempts'] + 1} started",
                             attempts=t["attempts"] + 1, contract_seen=self._version(c, t["feature"]),
                             started=now(), pid=None)
        return self.task(tid)

    def set_process(self, tid: int, pid: int | None, session_id: str | None) -> None:
        with self._tx() as c:
            self._task_row(c, tid)
            c.execute("UPDATE tasks SET pid=?, session_id=COALESCE(?, session_id), updated=? WHERE id=?",
                      (pid, session_id, now(), int(tid)))

    def mark_seen(self, tid: int) -> None:
        with self._tx() as c:
            t = self._task_row(c, tid)
            c.execute("UPDATE tasks SET contract_seen=? WHERE id=?", (self._version(c, t["feature"]), int(tid)))

    def complete(self, tid: int, summary: str, author: str) -> None:
        if not (summary or "").strip():
            raise AosError("summary is empty", "say what changed, the commits, and how it was tested")
        with self._tx() as c:
            t = self._task_row(c, tid)
            v = self._version(c, t["feature"])
            if t["status"] == "running" and (t["contract_seen"] or 0) < v:
                raise AosError(f"contract changed (v{t['contract_seen']}→v{v}) since you last read it",
                               "call board_read or task_show, re-check your work against the new "
                               "contract, then complete")
            self._transition(c, tid, ("running",), "done", author,
                             f"done: {summary.strip().splitlines()[0][:120]}",
                             result=summary.strip(), pid=None, note=None, resume=0)
            self._event(c, t["feature"], int(tid), "result", author, summary.strip())

    def block(self, tid: int, reason: str, author: str) -> None:
        if not (reason or "").strip():
            raise AosError("block reason is empty")
        with self._tx() as c:
            t = self._transition(c, tid, ("todo", "ready", "running"), "blocked", author,
                                 f"blocked: {reason.strip()[:120]}", pid=None)
            self._event(c, t["feature"], int(tid), "blocker", author, reason.strip())

    def unblock(self, tid: int, note: str = "", author: str = "human") -> None:
        with self._tx() as c:
            self._transition(c, tid, ("blocked",), "todo", author, "unblocked", note=note or None)
        self.promote()

    def attempt_failed(self, tid: int, reason: str) -> None:
        with self._tx() as c:
            t = self._task_row(c, tid)
            to = "failed" if t["attempts"] >= t["max_attempts"] else "ready"
            self._transition(c, tid, ("running",), to, "dispatcher",
                             f"attempt {t['attempts']} failed: {reason} → {to}", pid=None)

    def retry(self, tid: int, note: str | None = None, worker: str | None = None,
              resume: bool = False, author: str = "human") -> None:
        with self._tx() as c:
            t = self._task_row(c, tid)
            if resume and worker and worker != t["worker"]:
                raise AosError("--resume cannot switch worker tools", "retry without --resume to switch")
            self._transition(c, tid, ("failed", "blocked", "cancelled", "done", "ready"), "todo", author,
                             "retry requested" + (" (resume)" if resume else ""),
                             attempts=0, note=note, resume=1 if resume else 0,
                             worker=worker or t["worker"], pid=None)
        self.promote()

    def cancel(self, tid: int, author: str = "human") -> None:
        with self._tx() as c:
            self._transition(c, tid, ("todo", "ready", "running", "blocked", "failed"), "cancelled",
                             author, "cancelled")

    # -- contract proposals --------------------------------------------------
    def propose(self, tid: int, body: str, reason: str, author: str) -> int:
        if not (body or "").strip():
            raise AosError("proposal is empty")
        with self._tx() as c:
            t = self._task_row(c, tid)
            cur = c.execute("INSERT INTO proposals(feature, task, body, reason, status) VALUES (?,?,?,?,?)",
                            (t["feature"], int(tid), body.strip(), (reason or "").strip(), "pending"))
            pid = cur.lastrowid
            self._event(c, t["feature"], int(tid), "proposal", author,
                        f"proposal #{pid}: {(reason or '').strip()}\n{body.strip()}")
        return pid

    def proposals(self, feature: str | None = None, status: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM proposals WHERE 1=1", []
        if feature:
            sql, params = sql + " AND feature=?", params + [feature]
        if status:
            sql, params = sql + " AND status=?", params + [status]
        return self._rows(sql + " ORDER BY id", params)

    def _pending(self, c, pid: int):
        p = c.execute("SELECT * FROM proposals WHERE id=?", (int(pid),)).fetchone()
        if not p:
            raise AosError(f"unknown proposal #{pid}", "aos feature show <slug>")
        if p["status"] != "pending":
            raise AosError(f"proposal #{pid} is already {p['status']}")
        return p

    def approve(self, pid: int, new_contract: str | None = None, author: str = "human") -> dict:
        with self._tx() as c:
            p = self._pending(c, pid)
            v = self._version(c, p["feature"])
            nv = v + 1
            d = self.feature_dir(p["feature"])
            old = read_text(d / "contract.md")
            write_atomic(d / f"contract.v{v}.md", old)
            body = new_contract if new_contract is not None else (
                _contract_body(old).rstrip() + f"\n\n## Change v{nv} (proposal #{pid})\n\n{p['body']}\n")
            write_atomic(d / "contract.md", _contract_file(nv, body))
            c.execute("UPDATE features SET contract_version=? WHERE slug=?", (nv, p["feature"]))
            c.execute("UPDATE proposals SET status='approved', decided=? WHERE id=?", (now(), int(pid)))
            self._event(c, p["feature"], p["task"], "contract", author,
                        f"contract v{nv}: proposal #{pid} approved. Re-read the contract.")
        return {"proposal": int(pid), "contract_version": nv}

    def reject(self, pid: int, reason: str = "", author: str = "human") -> None:
        with self._tx() as c:
            p = self._pending(c, pid)
            c.execute("UPDATE proposals SET status='rejected', decided=? WHERE id=?", (now(), int(pid)))
            self._event(c, p["feature"], p["task"], "decision", author,
                        f"proposal #{pid} rejected" + (f": {reason}" if reason else ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_board.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add aos/board.py tests/test_board.py
git commit -m "feat(board): task transitions, retries and contract proposals"
```

---

### Task 3: Git worktrees

**Files:**
- Create: `aos/worktrees.py`
- Test: `tests/test_worktrees.py`

**Interfaces:**
- Consumes: `AosError`
- Produces: `worktree_path(home, feature, project) -> Path`, `branch_name(feature) -> str`, `ensure_worktree(project: Path, path: Path, branch: str) -> Path`

- [ ] **Step 1: Write the failing test**

`tests/test_worktrees.py`:
```python
import subprocess

import pytest

from aos.errors import AosError
from aos.worktrees import branch_name, ensure_worktree, worktree_path


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def test_paths(tmp_path):
    assert worktree_path(tmp_path, "checkout", "web") == tmp_path / "worktrees/checkout/web"
    assert branch_name("checkout") == "feature/checkout"


def test_creates_and_reuses(tmp_path, git_repo):
    proj = git_repo(tmp_path / "shop")
    wt = tmp_path / "wts/checkout/shop"
    assert ensure_worktree(proj, wt, "feature/checkout") == wt
    assert (wt / "README.md").exists()
    assert git(wt, "branch", "--show-current") == "feature/checkout"
    assert git(proj, "branch", "--show-current") == "main"
    (wt / "new.txt").write_text("x")
    git(wt, "add", ".")
    git(wt, "commit", "-qm", "work")
    assert ensure_worktree(proj, wt, "feature/checkout") == wt
    assert not (proj / "new.txt").exists()
    assert git(proj, "status", "--porcelain") == ""


def test_recreate_keeps_branch_commits(tmp_path, git_repo):
    proj = git_repo(tmp_path / "shop")
    wt = tmp_path / "wt"
    ensure_worktree(proj, wt, "feature/checkout")
    (wt / "new.txt").write_text("x")
    git(wt, "add", ".")
    git(wt, "commit", "-qm", "work")
    git(proj, "worktree", "remove", str(wt))
    ensure_worktree(proj, wt, "feature/checkout")
    assert (wt / "new.txt").exists()


def test_refusals(tmp_path, git_repo):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(AosError, match="not a git"):
        ensure_worktree(plain, tmp_path / "wt1", "feature/x")
    proj = git_repo(tmp_path / "shop")
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "f").write_text("")
    with pytest.raises(AosError, match="not a worktree"):
        ensure_worktree(proj, occupied, "feature/x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_worktrees.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aos.worktrees'`

- [ ] **Step 3: Implement**

`aos/worktrees.py`:
```python
"""Git worktrees for feature work: <home>/worktrees/<feature>/<project> on feature/<feature>.

The user's own checkout and current branch are never touched; an existing
feature branch is reused, never reset.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import AosError


def worktree_path(home: str | Path, feature: str, project: str) -> Path:
    return Path(home) / "worktrees" / feature / project


def branch_name(feature: str) -> str:
    return f"feature/{feature}"


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _common_dir(cwd: Path) -> Path | None:
    r = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd)
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 else None


def ensure_worktree(project: str | Path, path: str | Path, branch: str) -> Path:
    project, path = Path(project).resolve(), Path(path)
    common = _common_dir(project) if project.is_dir() else None
    if common is None:
        raise AosError(f"{project} is not a git repository", "board worktrees need git: git init + one commit")
    if path.exists():
        if _common_dir(path) != common:
            raise AosError(f"{path} exists but is not a worktree of {project}", "move it away, then retry the task")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = _git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], project).returncode == 0
    args = ["worktree", "add", str(path), branch] if exists else ["worktree", "add", "-b", branch, str(path)]
    r = _git(args, project)
    if r.returncode != 0:
        raise AosError(f"git worktree add failed: {r.stderr.strip()}", "the repo needs at least one commit")
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_worktrees.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add aos/worktrees.py tests/test_worktrees.py
git commit -m "feat(board): isolated git worktrees per feature and project"
```

---

### Task 4: Worker inputs and adapters (claude, kiro, command)

**Files:**
- Create: `aos/workers/__init__.py`, `aos/workers/common.py`, `aos/workers/claude.py`, `aos/workers/kiro.py`, `aos/workers/command.py`
- Test: `tests/test_workers.py`

**Interfaces:**
- Consumes: `AOS` (`.settings`, `.projects()`, `.repo`, `.data`, `.slug`), `build_context`, `Board` (Task 1-2), `load_user_config`, store helpers
- Produces:
  - `workers.common`: `WORKER_PROTOCOL`, `RunSpec`, `Launch(argv, cwd, env, session_id=None, cleanup=noop)`, `resolve_profile(aos, project, requested=None) -> str`, `profile_settings(aos, profile, project) -> dict` (keys: `adapter`, `timeout_min`, `max_attempts`, `allowed_tools`, `model`, …), `linked_project_path(project) -> Path`, `mcp_servers(project_path, task_id, aos_bin) -> dict`, `build_worker_context(aos, board, task) -> str`, `prepare_run(aos, board, task, worktree, project_path, aos_bin) -> (RunSpec, settings)`.
  - `workers.adapter(name) -> module`. Each adapter module has `prepare(spec, settings) -> Launch` and `EXIT_REASONS: dict[int, str]`.

- [ ] **Step 1: Write the failing test**

`tests/test_workers.py`:
```python
import json
import re
from pathlib import Path

import pytest
import yaml

from aos.board import Board
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.errors import AosError
from aos.workers import adapter, claude, command, kiro
from aos.workers.common import (
    build_worker_context, linked_project_path, mcp_servers, prepare_run, profile_settings,
    resolve_profile,
)


@pytest.fixture
def setup(configured, data, git_repo, tmp_path):
    proj = git_repo(tmp_path / "shop-api")
    save_user_config({**load_user_config(), "projects": {"shop-api": {"path": str(proj), "targets": ["claude"]}}})
    aos = AOS(configured, slug="shop-api")
    aos.register_project("shop-api", "Shop API")
    board = Board(data)
    board.create_feature("checkout", "Checkout v2", brief="Goal: new checkout", contract="POST /orders")
    a = board.add_task("checkout", "shop-api", "Orders endpoint", spec="Implement POST /orders")
    b = board.add_task("checkout", "shop-api", "Docs", spec="Document it", depends_on=[a])
    board.promote()
    board.claim(a)
    board.complete(a, "endpoint live", author="w")
    board.promote()
    task = board.claim(b)
    return aos, board, task, proj, tmp_path / "wt"


def set_worker(aos, **worker):
    (aos.data / "projects.yaml").write_text(yaml.safe_dump(
        {"projects": {"shop-api": {"description": "Shop API", "worker": worker}}}))


def test_profile_resolution_and_settings(setup):
    aos, *_ = setup
    assert resolve_profile(aos, "shop-api") == "claude"
    set_worker(aos, profile="kiro", allowed_tools=["shell:npm test"])
    assert resolve_profile(aos, "shop-api") == "kiro"
    assert resolve_profile(aos, "shop-api", "claude") == "claude"
    s = profile_settings(aos, "kiro", "shop-api")
    assert s["adapter"] == "kiro" and s["timeout_min"] == 45 and s["max_attempts"] == 2
    assert "shell:npm test" in s["allowed_tools"] and "mcp:aos" in s["allowed_tools"]
    with pytest.raises(AosError):
        profile_settings(aos, "nope", "shop-api")
    set_worker(aos, allowed_tools=["rm -rf /"])
    with pytest.raises(AosError):
        profile_settings(aos, "claude", "shop-api")


def test_linked_project_path(setup):
    _, _, _, proj, _ = setup
    assert linked_project_path("shop-api") == proj
    with pytest.raises(AosError, match="not linked"):
        linked_project_path("ghost")


def test_context_has_general_and_specific_parts(setup):
    aos, board, task, _, _ = setup
    ctx = build_worker_context(aos, board, task)
    for s in ["You are the team agent.", "Board worker protocol", "Checkout v2", "Goal: new checkout",
              "Contract v1", "POST /orders", "#2 (shop-api): Docs", "Document it", "endpoint live",
              "Recent feature timeline"]:
        assert s in ctx, s


def test_mcp_servers_include_graphskill(setup):
    _, _, _, proj, _ = setup
    assert set(mcp_servers(proj, 7, "/bin/aos")) == {"aos"}
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"graphskill": {"command": "gs"}}}))
    s = mcp_servers(proj, 7, "/bin/aos")
    assert s["aos"] == {"command": "/bin/aos", "args": ["serve", "--project", str(proj), "--task", "7"]}
    assert s["graphskill"] == {"command": "gs"}


def test_prepare_run_writes_context_and_prompt(setup):
    aos, board, task, proj, wt = setup
    spec, settings = prepare_run(aos, board, task, wt, proj, "/bin/aos")
    assert spec.context_file.read_text().startswith("# agenticOS context")
    assert spec.prompt.startswith(f"Do task #{task['id']}: Docs")
    assert spec.worktree == wt and spec.resume is False and settings["adapter"] == "claude"
    board.attempt_failed(task["id"], "test")
    board.retry(task["id"], note="add examples")
    board.claim(task["id"])
    spec, _ = prepare_run(aos, board, board.task(task["id"]), wt, proj, "/bin/aos")
    assert "add examples" in spec.prompt


def test_claude_adapter_argv(setup):
    aos, board, task, proj, wt = setup
    spec, settings = prepare_run(aos, board, task, wt, proj, "/bin/aos")
    launch = adapter("claude").prepare(spec, settings)
    argv = launch.argv
    assert argv[:3] == ["claude", "-p", spec.prompt]
    assert argv[argv.index("--append-system-prompt") + 1] == spec.context_file.read_text()
    mcp = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text())
    assert mcp["mcpServers"]["aos"]["args"][-1] == str(task["id"])
    assert "--strict-mcp-config" in argv and argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--session-id") + 1] == launch.session_id
    tools = argv[argv.index("--allowedTools") + 1:]
    assert tools[:5] == ["Read", "Glob", "Grep", "Edit", "Write"]
    assert "Bash(git commit:*)" in tools and "mcp__aos" in tools
    assert not {"bypassPermissions", "--dangerously-skip-permissions"} & set(argv)
    assert launch.cwd == wt
    spec.resume, spec.session_id = True, "abc"
    argv = claude.prepare(spec, settings).argv
    assert argv[argv.index("--resume") + 1] == "abc" and "--session-id" not in argv


def test_kiro_adapter_agent_file(setup, tmp_path):
    aos, board, task, proj, wt = setup
    spec, _ = prepare_run(aos, board, task, wt, proj, "/bin/aos")
    settings = {**profile_settings(aos, "kiro", "shop-api"), "agents_dir": str(tmp_path / "agents")}
    launch = kiro.prepare(spec, settings)
    name = f"aos-checkout-t{task['id']}"
    assert launch.argv == ["kiro-cli", "chat", "--no-interactive", "--agent", name,
                           "--require-mcp-startup", spec.prompt]
    assert "--trust-all-tools" not in launch.argv and launch.cwd == wt
    path = tmp_path / "agents" / f"{name}.json"
    cfg = json.loads(path.read_text())
    assert cfg["name"] == name and cfg["prompt"] == f"file://{spec.context_file}"
    assert cfg["includeMcpJson"] is False and set(cfg["mcpServers"]) == {"aos"}
    assert cfg["tools"] == ["read", "write", "@aos", "shell"]
    assert cfg["allowedTools"] == ["read", "write", "@aos"]
    rules = cfg["toolsSettings"]["shell"]["allowedCommands"]
    assert any(re.match(r, "git commit -m x") for r in rules)
    assert not any(re.match(r, "git commitx") for r in rules)
    assert not any(re.match(r, "rm -rf /") for r in rules)
    assert "model" not in cfg
    launch.cleanup()
    assert not path.exists()
    spec.resume = True
    assert "--resume" in kiro.prepare(spec, settings).argv
    assert kiro.EXIT_REASONS[3] == "MCP startup failed"


def test_command_adapter(setup):
    aos, board, task, proj, wt = setup
    spec, settings = prepare_run(aos, board, task, wt, proj, "/bin/aos")
    launch = command.prepare(spec, {**settings, "command": ["run-me", "{task_id}", "--in={worktree}"]})
    assert launch.argv == ["run-me", str(task["id"]), f"--in={wt}"]
    with pytest.raises(AosError):
        command.prepare(spec, {**settings, "command": ["x", "{nope}"]})
    with pytest.raises(AosError):
        command.prepare(spec, {**settings, "command": None})
    with pytest.raises(AosError):
        adapter("cursor")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_workers.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aos.workers'`

- [ ] **Step 3: Implement**

`aos/workers/common.py`:
```python
"""What every board worker gets, whichever tool runs it (spec §6.1)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..board import Board
from ..config import load_user_config
from ..context import build_context
from ..core import AOS
from ..errors import AosError
from ..store import read_text, write_atomic

WORKER_PROTOCOL = """\
You are an agenticOS board worker running headless on one task of a multi-project feature. Other workers handle the other projects in parallel.
1. Call task_show first.
2. Call board_read before each step and before finishing; follow contract changes and other workers' notes.
3. Work only inside the current directory (a git worktree on the feature branch). Commit with tests on the current branch. Never push. Never edit other projects.
4. If the shared contract must change, call task_propose_contract and continue with the parts it doesn't affect. If nothing is left that you can do, call task_block with the reason.
5. Finish with task_complete(summary): what changed, the commits, how it was tested, and anything dependent tasks must know."""

NEUTRAL_TOOL = re.compile(r"^(read|write|shell:\S.*|mcp:[A-Za-z0-9_-]+)$")


@dataclass
class RunSpec:
    task: dict
    project_path: Path
    worktree: Path
    run_dir: Path
    context_file: Path
    prompt: str
    mcp_servers: dict
    allowed: list[str]
    model: str | None
    resume: bool
    session_id: str | None


@dataclass
class Launch:
    argv: list[str]
    cwd: Path
    env: dict
    session_id: str | None = None
    cleanup: Callable[[], None] = field(default=lambda: None)


def _project_worker(aos: AOS, project: str) -> dict:
    return ((aos.projects().get(project) or {}).get("worker")) or {}


def resolve_profile(aos: AOS, project: str, requested: str | None = None) -> str:
    return requested or _project_worker(aos, project).get("profile") or aos.settings["board"]["default_worker"]


def profile_settings(aos: AOS, profile: str, project: str) -> dict:
    workers = aos.settings["workers"]
    if profile == "common" or profile not in workers:
        raise AosError(f"unknown worker profile {profile!r}", "define it under workers: in aos.yaml")
    common, own = workers.get("common") or {}, workers[profile] or {}
    s = {**common, **own}
    tools = [*common.get("allowed_tools", []), *own.get("allowed_tools", []),
             *_project_worker(aos, project).get("allowed_tools", [])]
    for t in tools:
        if not NEUTRAL_TOOL.match(str(t)):
            raise AosError(f"invalid allowed tool {t!r}", "use read, write, shell:<command prefix> or mcp:<server>")
    s["allowed_tools"] = list(dict.fromkeys(tools))
    s.setdefault("adapter", profile)
    return s


def linked_project_path(project: str) -> Path:
    info = load_user_config()["projects"].get(project)
    if not info:
        raise AosError(f"project {project!r} is not linked on this machine", f"aos link <path> --name {project}")
    return Path(info["path"])


def mcp_servers(project_path: Path, task_id: int, aos_bin: str) -> dict:
    servers = {"aos": {"command": aos_bin, "args": ["serve", "--project", str(project_path), "--task", str(task_id)]}}
    try:
        gs = (json.loads(read_text(Path(project_path) / ".mcp.json") or "{}").get("mcpServers") or {}).get("graphskill")
    except json.JSONDecodeError:
        gs = None
    if gs:
        servers["graphskill"] = gs
    return servers


def build_worker_context(aos: AOS, board: Board, task: dict) -> str:
    feature = board.feature(task["feature"])
    contract = board.contract(task["feature"])
    parts = [
        build_context(aos).rstrip(),
        "## Board worker protocol\n\n" + WORKER_PROTOCOL,
        f"## Feature {feature['slug']}: {feature['title']}\n\n{board.brief(feature['slug']).strip() or '(no brief)'}",
        f"## Contract v{contract['version']}\n\n{contract['text'].strip() or '(no contract yet)'}",
        f"## Your task #{task['id']} ({task['project']}): {task['title']}\n\n{(task['spec'] or '').strip() or '(no spec)'}",
    ]
    deps = board.dependency_results(task["id"])
    if deps:
        parts.append("## Results of tasks you depend on\n\n" + "\n\n".join(
            f"### #{d['id']} {d['project']}: {d['title']}\n{(d['result'] or '').strip()}" for d in deps))
    events = board.events(task["feature"], limit=30)
    if events:
        parts.append("## Recent feature timeline\n\n" + "\n".join(
            f"- [{e['id']}] {e['author']} {e['kind']}: {(e['body'] or '').splitlines()[0] if e['body'] else ''}"
            for e in events))
    return "\n\n".join(parts) + "\n"


def prepare_run(aos: AOS, board: Board, task: dict, worktree: Path, project_path: Path,
                aos_bin: str) -> tuple[RunSpec, dict]:
    settings = profile_settings(aos, task["worker"], task["project"])
    run_dir = board.data / "runs" / f"{task['id']}-{task['attempts']}"
    context_file = run_dir / "context.md"
    write_atomic(context_file, build_worker_context(aos, board, task))
    base = f"Do task #{task['id']}: {task['title']}. Follow the board worker protocol."
    note = (task.get("note") or "").strip()
    if task.get("resume") and note:
        prompt = note
    elif note:
        prompt = f"{base}\n\nNote from the human: {note}"
    else:
        prompt = base
    spec = RunSpec(task=task, project_path=Path(project_path), worktree=Path(worktree), run_dir=run_dir,
                   context_file=context_file, prompt=prompt,
                   mcp_servers=mcp_servers(project_path, task["id"], aos_bin),
                   allowed=settings["allowed_tools"], model=settings.get("model"),
                   resume=bool(task.get("resume")), session_id=task.get("session_id"))
    return spec, settings


def write_mcp_config(spec: RunSpec) -> Path:
    path = spec.run_dir / "mcp.json"
    write_atomic(path, json.dumps({"mcpServers": spec.mcp_servers}, indent=2) + "\n")
    return path
```

`aos/workers/claude.py`:
```python
"""Claude Code worker: everything passed as flags (spec §6.2)."""

from __future__ import annotations

import os
import uuid

from ..errors import AosError
from ..store import read_text
from .common import Launch, RunSpec, write_mcp_config

EXIT_REASONS: dict[int, str] = {}


def translate(allowed: list[str]) -> list[str]:
    out: list[str] = []
    for a in allowed:
        if a == "read":
            out += ["Read", "Glob", "Grep"]
        elif a == "write":
            out += ["Edit", "Write"]
        elif a.startswith("shell:"):
            out.append(f"Bash({a[6:]}:*)")
        elif a.startswith("mcp:"):
            out.append(f"mcp__{a[4:]}")
        else:
            raise AosError(f"invalid allowed tool {a!r}")
    return list(dict.fromkeys(out))


def prepare(spec: RunSpec, settings: dict) -> Launch:
    mcp = write_mcp_config(spec)
    argv = [settings.get("bin") or "claude", "-p", spec.prompt,
            "--append-system-prompt", read_text(spec.context_file),
            "--mcp-config", str(mcp), "--strict-mcp-config",
            "--permission-mode", "acceptEdits"]
    if settings.get("model"):
        argv += ["--model", str(settings["model"])]
    if spec.resume and spec.session_id:
        session = spec.session_id
        argv += ["--resume", session]
    else:
        session = str(uuid.uuid4())
        argv += ["--session-id", session]
    argv += ["--allowedTools", *translate(spec.allowed)]
    return Launch(argv=argv, cwd=spec.worktree, env=dict(os.environ), session_id=session)
```

`aos/workers/kiro.py`:
```python
"""Kiro worker: a per-run agent file plus `kiro-cli chat --no-interactive` (spec §6.3).

Not yet verified against a live kiro-cli: the shell allow-list uses the legacy
toolsSettings.shell.allowedCommands regex form, and no session id is recorded
(resume continues the worktree's last session).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..errors import AosError
from ..store import write_atomic
from .common import Launch, RunSpec

EXIT_REASONS = {3: "MCP startup failed"}


def agents_dir(settings: dict) -> Path:
    return Path(settings.get("agents_dir") or Path.home() / ".kiro" / "agents").expanduser()


def agent_name(task: dict) -> str:
    return f"aos-{task['feature']}-t{task['id']}"


def agent_config(spec: RunSpec, settings: dict) -> dict:
    tools, allowed, shell = ["read", "write"], [], []
    for a in spec.allowed:
        if a in ("read", "write"):
            allowed.append(a)
        elif a.startswith("shell:"):
            shell.append(rf"^{re.escape(a[6:])}(\s.*)?$")
        elif a.startswith("mcp:"):
            if a[4:] in spec.mcp_servers:
                tools.append(f"@{a[4:]}")
                allowed.append(f"@{a[4:]}")
        else:
            raise AosError(f"invalid allowed tool {a!r}")
    if shell:
        tools.append("shell")
    cfg = {
        "name": agent_name(spec.task),
        "description": f"agenticOS worker for task #{spec.task['id']}",
        "prompt": f"file://{spec.context_file}",
        "mcpServers": spec.mcp_servers,
        "includeMcpJson": False,
        "tools": list(dict.fromkeys(tools)),
        "allowedTools": list(dict.fromkeys(allowed)),
    }
    if shell:
        cfg["toolsSettings"] = {"shell": {"allowedCommands": shell}}
    if settings.get("model"):
        cfg["model"] = settings["model"]
    return cfg


def prepare(spec: RunSpec, settings: dict) -> Launch:
    name = agent_name(spec.task)
    path = agents_dir(settings) / f"{name}.json"
    write_atomic(path, json.dumps(agent_config(spec, settings), indent=2) + "\n")
    argv = [settings.get("bin") or "kiro-cli", "chat", "--no-interactive", "--agent", name,
            "--require-mcp-startup"]
    if spec.resume:
        argv.append("--resume")
    argv.append(spec.prompt)
    return Launch(argv=argv, cwd=spec.worktree, env=dict(os.environ), session_id=None,
                  cleanup=lambda: path.unlink(missing_ok=True))
```

`aos/workers/command.py`:
```python
"""Generic argv-template worker (tests, other agent CLIs). Never goes through a shell."""

from __future__ import annotations

import os

from ..errors import AosError
from .common import Launch, RunSpec, write_mcp_config

EXIT_REASONS: dict[int, str] = {}


def prepare(spec: RunSpec, settings: dict) -> Launch:
    template = settings.get("command")
    if not isinstance(template, list) or not template:
        raise AosError("command worker needs workers.<profile>.command as a list of arguments")
    values = {"prompt": spec.prompt, "context_file": str(spec.context_file),
              "mcp_config": str(write_mcp_config(spec)), "task_id": str(spec.task["id"]),
              "worktree": str(spec.worktree)}
    try:
        argv = [str(part).format_map(values) for part in template]
    except (KeyError, IndexError, ValueError) as e:
        raise AosError(f"bad placeholder in worker command: {e}", "use {prompt} {context_file} {mcp_config} {task_id} {worktree}")
    return Launch(argv=argv, cwd=spec.worktree, env=dict(os.environ))
```

`aos/workers/__init__.py`:
```python
"""Worker adapters: one per agent tool, all fed by workers.common."""

from ..errors import AosError
from . import claude, command, kiro

ADAPTERS = {"claude": claude, "kiro": kiro, "command": command}


def adapter(name: str):
    if name not in ADAPTERS:
        raise AosError(f"unknown worker adapter {name!r}", "one of: " + ", ".join(ADAPTERS))
    return ADAPTERS[name]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_workers.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add aos/workers tests/test_workers.py
git commit -m "feat(board): worker inputs and Claude Code / Kiro / command adapters"
```

---

### Task 5: Dispatcher

**Files:**
- Create: `aos/dispatcher.py`, `tests/fake_worker.py`
- Test: `tests/test_dispatcher.py`

**Interfaces:**
- Consumes: `AOS`, `Board`, `ensure_worktree`, `worktree_path`, `branch_name`, `workers.adapter`, `workers.common.prepare_run`, `linked_project_path`, `AosError`
- Produces: `Dispatcher(repo, *, feature=None, parallel=None, tick=2.0)` with `.tick()`, `.run(once=False)`, `.recover()`, `.running: dict[int, Running]`, `._terminate(running)`, `._lock()`

- [ ] **Step 1: Write the fake worker and the failing test**

`tests/fake_worker.py`:
```python
"""Fake board worker for dispatcher tests. Mode from FAKE_MODE_<task id> or FAKE_MODE."""

import os
import sys
import time
from pathlib import Path

from aos.board import Board
from aos.config import data_root

tid = int(sys.argv[1])
mode = os.environ.get(f"FAKE_MODE_{tid}", os.environ.get("FAKE_MODE", "complete"))
board = Board(data_root())
if mode == "crash":
    sys.exit(2)
if mode == "noop":
    sys.exit(0)
if mode == "sleep":
    time.sleep(float(os.environ.get("FAKE_SLEEP", "30")))
    sys.exit(0)
if mode == "block":
    board.block(tid, "fake blocker", author=f"task:{tid}")
    sys.exit(0)
if mode == "write":
    (Path.cwd() / f"t{tid}.txt").write_text("work\n")
board.mark_seen(tid)
board.complete(tid, f"done by fake worker in {Path.cwd()}", author=f"task:{tid}")
```

`tests/test_dispatcher.py`:
```python
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from aos.board import Board
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.dispatcher import Dispatcher
from aos.errors import AosError

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).parent / "fake_worker.py"


@pytest.fixture
def env(configured, data, home, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    (configured / "aos.yaml").write_text(yaml.safe_dump({
        "board": {"default_worker": "fake", "parallel": 2},
        "workers": {"fake": {"adapter": "command", "timeout_min": 0.02,
                             "command": [sys.executable, str(FAKE), "{task_id}"]}}}))
    projects = {}
    for name in ("api", "web", "billing"):
        projects[name] = {"path": str(git_repo(tmp_path / name)), "targets": ["claude"]}
        AOS(configured).register_project(name)
    save_user_config({**load_user_config(), "projects": projects})
    board = Board(data)
    board.create_feature("checkout", "Checkout")
    return configured, board, tmp_path, home


def add(board, project, title="t", deps=(), max_attempts=2):
    return board.add_task("checkout", project, title, depends_on=deps, worker="fake", max_attempts=max_attempts)


def stop_all(d):
    for r in list(d.running.values()):
        d._terminate(r)
    d._reap()


def test_parallel_cap_and_one_per_worktree(env, monkeypatch):
    repo, board, _, _ = env
    monkeypatch.setenv("FAKE_MODE", "sleep")
    ids = [add(board, p) for p in ("api", "web", "billing", "api")]
    d = Dispatcher(repo, tick=0.05)
    try:
        d.tick()
        assert len(d.running) == 2
        d.parallel = 4
        d.tick()
        running = {board.task(t)["project"] for t in d.running}
        assert len(d.running) == 3 and running == {"api", "web", "billing"}
        assert board.task(ids[3])["status"] == "ready"
    finally:
        stop_all(d)


def test_once_runs_dependency_chain(env):
    repo, board, tmp_path, home = env
    a = add(board, "api")
    b = add(board, "web", deps=[a])
    Dispatcher(repo, tick=0.05).run(once=True)
    assert board.task(a)["status"] == "done" and board.task(b)["status"] == "ready"
    Dispatcher(repo, tick=0.05).run(once=True)
    assert board.task(b)["status"] == "done"
    assert "done by fake worker in" in board.task(b)["result"]
    assert (home / "worktrees/checkout/web").is_dir()
    head = subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path / "web",
                          capture_output=True, text=True).stdout.strip()
    assert head == "main"


def test_worker_writes_only_in_worktree(env, monkeypatch):
    repo, board, tmp_path, home = env
    monkeypatch.setenv("FAKE_MODE", "write")
    t = add(board, "api")
    Dispatcher(repo, tick=0.05).run(once=True)
    assert (home / f"worktrees/checkout/api/t{t}.txt").exists()
    assert not (tmp_path / f"api/t{t}.txt").exists()


def test_failures_retry_then_failed(env, monkeypatch):
    repo, board, _, _ = env
    monkeypatch.setenv("FAKE_MODE", "crash")
    t = add(board, "api", max_attempts=2)
    Dispatcher(repo, tick=0.05).run(once=True)
    assert board.task(t)["status"] == "ready" and board.task(t)["attempts"] == 1
    Dispatcher(repo, tick=0.05).run(once=True)
    assert board.task(t)["status"] == "failed"
    assert any("exited with code 2" in e["body"] for e in board.events("checkout"))
    assert list((board.data / "logs").glob(f"{t}-*.log"))


def test_exit_zero_without_complete(env, monkeypatch):
    repo, board, _, _ = env
    monkeypatch.setenv("FAKE_MODE", "noop")
    t = add(board, "api", max_attempts=1)
    Dispatcher(repo, tick=0.05).run(once=True)
    assert board.task(t)["status"] == "failed"
    assert any("exited without task_complete" in e["body"] for e in board.events("checkout"))


def test_timeout_kills_worker(env, monkeypatch):
    repo, board, _, _ = env
    monkeypatch.setenv("FAKE_MODE", "sleep")
    t = add(board, "api", max_attempts=1)
    start = time.monotonic()
    Dispatcher(repo, tick=0.05).run(once=True)
    assert time.monotonic() - start < 10
    assert board.task(t)["status"] == "failed"
    assert any("timed out" in e["body"] for e in board.events("checkout"))


def test_block_unlinked_project_and_worker_block(env, monkeypatch):
    repo, board, _, _ = env
    ghost = add(board, "ghost")
    monkeypatch.setenv("FAKE_MODE", "block")
    t = add(board, "api")
    Dispatcher(repo, tick=0.05).run(once=True)
    assert board.task(ghost)["status"] == "blocked"
    assert any("not linked" in e["body"] for e in board.events("checkout") if e["task"] == ghost)
    assert board.task(t)["status"] == "blocked"


def test_cancel_terminates_running_worker(env, monkeypatch):
    repo, board, _, _ = env
    monkeypatch.setenv("FAKE_MODE", "sleep")
    t = add(board, "api")
    d = Dispatcher(repo, tick=0.05)
    d.tick()
    proc = d.running[t].proc
    board.cancel(t)
    d.tick()
    assert proc.poll() is not None and t not in d.running
    assert board.task(t)["status"] == "cancelled"


def test_recover_marks_orphans(env):
    repo, board, _, _ = env
    t = add(board, "api")
    board.promote()
    board.claim(t)
    dead = subprocess.Popen(["true"])
    dead.wait()
    board.set_process(t, dead.pid, None)
    Dispatcher(repo, tick=0.05).recover()
    assert board.task(t)["status"] == "ready"
    assert any("restarted" in e["body"] for e in board.events("checkout"))


def test_single_dispatcher_lock(env):
    repo, _, _, _ = env
    d1 = Dispatcher(repo, tick=0.05)
    with d1._lock():
        with pytest.raises(AosError, match="already running"):
            Dispatcher(repo, tick=0.05).run(once=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dispatcher.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aos.dispatcher'`

- [ ] **Step 3: Implement**

`aos/dispatcher.py`:
```python
"""`aos board run`: launch ready tasks as headless workers, in parallel across projects.

Each tick: reap finished/timed-out/cancelled workers, promote tasks whose
dependencies are done, then launch ready tasks up to the parallel limit with at
most one running task per (feature, project) worktree.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import signal
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .board import Board
from .core import AOS
from .errors import AosError
from .workers import adapter
from .workers.common import Launch, linked_project_path, prepare_run
from .worktrees import branch_name, ensure_worktree, worktree_path


@dataclass
class Running:
    proc: subprocess.Popen
    launch: Launch
    deadline: float
    timeout_min: float
    log: IO
    exit_reasons: dict
    timed_out: bool = False


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Dispatcher:
    def __init__(self, repo: str | Path, *, feature: str | None = None, parallel: int | None = None,
                 tick: float = 2.0):
        self.aos = AOS(repo)
        board_cfg = self.aos.settings["board"]
        self.board = Board(self.aos.data, board_cfg["max_tasks_per_feature"])
        self.feature = feature
        self.parallel = int(parallel or board_cfg["parallel"])
        self.tick_s = tick
        self.running: dict[int, Running] = {}
        self.stopping = False
        self.aos_bin = shutil.which("aos") or "aos"

    # -- lifecycle -----------------------------------------------------------
    @contextmanager
    def _lock(self):
        path = self.board.data / ".dispatcher.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            raise AosError("another `aos board run` is already running")
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()

    def recover(self) -> None:
        """Tasks left `running` by a previous dispatcher: stop any survivor, count a failed attempt."""
        for t in self.board.tasks(status="running"):
            if t["pid"] and _alive(t["pid"]):
                try:
                    os.killpg(t["pid"], signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            self.board.attempt_failed(t["id"], "dispatcher restarted; previous worker abandoned")

    def run(self, once: bool = False) -> None:
        with self._lock():
            self.recover()
            self.tick()
            if once:
                while self.running:
                    time.sleep(self.tick_s)
                    self._reap()
                self.board.promote()
                return
            previous = signal.signal(signal.SIGINT, self._on_sigint)
            try:
                while not (self.stopping and not self.running):
                    time.sleep(self.tick_s)
                    self.tick()
            finally:
                signal.signal(signal.SIGINT, previous)

    def _on_sigint(self, *_):
        if self.stopping:
            for r in list(self.running.values()):
                self._terminate(r)
        self.stopping = True

    # -- one tick ------------------------------------------------------------
    def tick(self) -> None:
        self._reap()
        self.board.promote()
        if not self.stopping:
            self._launch_ready()

    def _terminate(self, r: Running) -> None:
        for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
            try:
                os.killpg(r.proc.pid, sig)
            except ProcessLookupError:
                return
            try:
                r.proc.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue

    def _reap(self) -> None:
        for tid, r in list(self.running.items()):
            code = r.proc.poll()
            if code is None:
                status = self.board.task(tid)["status"]
                if status == "cancelled":
                    self._terminate(r)
                elif time.monotonic() > r.deadline:
                    r.timed_out = True
                    self._terminate(r)
                code = r.proc.poll()
                if code is None:
                    continue
            r.launch.cleanup()
            r.log.close()
            del self.running[tid]
            if self.board.task(tid)["status"] != "running":
                continue
            if r.timed_out:
                why = f"timed out after {r.timeout_min:g} min"
            elif code in r.exit_reasons:
                why = r.exit_reasons[code]
            elif code == 0:
                why = "exited without task_complete"
            else:
                why = f"exited with code {code}"
            self.board.attempt_failed(tid, why)

    def _launch_ready(self) -> None:
        busy = {(t["feature"], t["project"]) for t in self.board.tasks(status="running")}
        for t in self.board.tasks(feature=self.feature, status="ready"):
            if len(self.running) >= self.parallel:
                break
            key = (t["feature"], t["project"])
            if key in busy:
                continue
            busy.add(key)
            self._launch(t)

    def _launch(self, t: dict) -> None:
        tid = t["id"]
        try:
            project_path = linked_project_path(t["project"])
            wt = ensure_worktree(project_path, worktree_path(self.aos.home, t["feature"], t["project"]),
                                 branch_name(t["feature"]))
        except AosError as e:
            self.board.block(tid, e.message + (f" ({e.hint})" if e.hint else ""), author="dispatcher")
            return
        task = self.board.claim(tid)
        launch = None
        try:
            aos = AOS(self.aos.repo, home=self.aos.home, slug=t["project"], data=self.aos.data)
            spec, settings = prepare_run(aos, self.board, task, wt, project_path, self.aos_bin)
            mod = adapter(settings["adapter"])
            launch = mod.prepare(spec, settings)
            log_path = self.board.data / "logs" / f"{tid}-{task['attempts']}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log = open(log_path, "ab")
            proc = subprocess.Popen(launch.argv, cwd=launch.cwd, env=launch.env, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        except Exception as e:  # any launch failure is a failed attempt, never a crash of the loop
            if launch:
                launch.cleanup()
            self.board.attempt_failed(tid, f"launch failed: {e}")
            return
        self.board.set_process(tid, proc.pid, launch.session_id)
        timeout = float(settings["timeout_min"])
        self.running[tid] = Running(proc, launch, time.monotonic() + 60 * timeout, timeout, log,
                                    getattr(mod, "EXIT_REASONS", {}))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_dispatcher.py -q`
Expected: all PASS. The timeout test takes about 1-2 s.

- [ ] **Step 5: Commit**

```bash
git add aos/dispatcher.py tests/fake_worker.py tests/test_dispatcher.py
git commit -m "feat(board): dispatcher with parallel workers, retries, timeouts and recovery"
```

---

### Task 6: Board MCP tools (worker and planner mode)

**Files:**
- Modify: `aos/mcp_server.py`
- Test: `tests/test_mcp_board.py`

**Interfaces:**
- Consumes: `Board`, `AOS`, `resolve_profile`, `profile_settings`, `_safe`, `Tools`
- Produces:
  - `BoardTools(aos, task_id=None)`.
    - Worker-mode methods: `task_show, board_read, task_comment, task_block, task_propose_contract, task_create, task_complete`.
    - Planner-mode methods: `feature_create, feature_show, task_create, board_read`.
  - `WORKER_TOOLS`, `PLANNER_TOOLS` (name sets).
  - `build_server(tools, board_tools=None)`, `run_server(project, task=None)`.

- [ ] **Step 1: Write the failing test**

`tests/test_mcp_board.py`:
```python
import asyncio

import pytest

from aos.board import Board
from aos.core import AOS
from aos.mcp_server import PLANNER_TOOLS, WORKER_TOOLS, BoardTools, Tools, build_server


@pytest.fixture
def planner(configured, data):
    aos = AOS(configured)
    for p in ("api", "web"):
        aos.register_project(p)
    return BoardTools(aos)


def test_planner_creates_feature_and_tasks(planner):
    assert planner.feature_create("checkout", "Checkout v2", brief="Goal", contract="POST /orders")["slug"] == "checkout"
    a = planner.task_create("api", "Orders endpoint", spec="do it", feature="checkout")["task"]
    b = planner.task_create("web", "Use it", feature="checkout", depends_on=[a])["task"]
    shown = planner.feature_show("checkout")
    assert [t["id"] for t in shown["tasks"]] == [a, b] and shown["contract"]["version"] == 1
    assert shown["tasks"][0]["worker"] == "claude"
    assert "error" in planner.task_create("ghost", "x", feature="checkout")
    assert "error" in planner.task_create("api", "x")
    assert planner.board_read(feature="checkout")["events"]


@pytest.fixture
def worker(planner):
    planner.feature_create("checkout", "Checkout v2", contract="POST /orders")
    tid = planner.task_create("api", "Orders endpoint", feature="checkout")["task"]
    board = planner.board
    board.promote()
    board.claim(tid)
    return BoardTools(AOS(planner.aos.repo, slug="api"), task_id=tid), board, tid


def test_worker_show_comment_create(worker):
    w, board, tid = worker
    shown = w.task_show()
    assert shown["task"]["id"] == tid and shown["contract"]["text"].startswith("POST /orders")
    assert w.task_comment("halfway")["ok"]
    follow = w.task_create("web", "Consume endpoint", spec="call it")["task"]
    assert board.task(follow)["feature"] == "checkout" and board.task(follow)["created_by"] == f"task:{tid}"
    assert "error" in w.task_create("ghost", "x")


def test_worker_complete_refused_until_seen(worker):
    w, board, tid = worker
    p = w.task_propose_contract("add currency", "multi-currency")["proposal"]
    board.approve(p)
    r = w.task_complete("all done")
    assert "error" in r and "v1→v2" in r["error"]
    read = w.board_read()
    assert read["contract_version"] == 2 and any(e["kind"] == "contract" for e in read["events"])
    assert w.task_complete("all done")["ok"]
    assert board.task(tid)["status"] == "done"


def test_worker_block(worker):
    w, board, tid = worker
    assert w.task_block("missing credentials")["ok"]
    assert board.task(tid)["status"] == "blocked"


def test_tool_registration_by_mode(planner, worker):
    w, _, _ = worker
    base = Tools(planner.aos)
    names = {t.name for t in asyncio.run(build_server(base, planner).list_tools())}
    assert PLANNER_TOOLS <= names and not (WORKER_TOOLS - PLANNER_TOOLS) & names
    names = {t.name for t in asyncio.run(build_server(base, w).list_tools())}
    assert WORKER_TOOLS <= names and "feature_create" not in names
    assert len({t.name for t in asyncio.run(build_server(base).list_tools())}) == 11
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mcp_board.py -q`
Expected: FAIL with `ImportError: cannot import name 'PLANNER_TOOLS'`

- [ ] **Step 3: Implement**

In `aos/mcp_server.py`, add imports:
```python
from .board import Board
from .workers.common import profile_settings, resolve_profile
```

Add after class `Tools`:
```python
WORKER_TOOLS = {"task_show", "board_read", "task_comment", "task_block", "task_propose_contract",
                "task_create", "task_complete"}
PLANNER_TOOLS = {"feature_create", "feature_show", "task_create", "board_read"}


class BoardTools:
    """Board access over MCP. With task_id: a worker's tools for its own task.
    Without: planner tools for creating features and tasks."""

    def __init__(self, aos: AOS, task_id: int | None = None):
        self.aos = aos
        self.board = Board(aos.data, aos.settings["board"]["max_tasks_per_feature"])
        self.task_id = int(task_id) if task_id is not None else None

    @property
    def author(self) -> str:
        return f"task:{self.task_id}" if self.task_id else "planner"

    def _own(self) -> dict:
        if not self.task_id:
            raise AosError("this tool is only available to board workers")
        return self.board.task(self.task_id)

    def _new_task(self, feature: str, project: str, title: str, spec: str, depends_on) -> dict:
        if project not in self.aos.projects():
            raise AosError(f"unknown project {project!r}", "call project_list; link it with aos link")
        profile = resolve_profile(self.aos, project)
        attempts = profile_settings(self.aos, profile, project)["max_attempts"]
        tid = self.board.add_task(feature, project, title, spec, depends_on or [], worker=profile,
                                  max_attempts=attempts, created_by=self.author)
        return {"task": tid, "worker": profile}

    # -- worker mode ---------------------------------------------------------
    @_safe
    def task_show(self) -> dict:
        t = self._own()
        self.board.mark_seen(t["id"])
        return {"task": t, "feature": self.board.feature(t["feature"]), "brief": self.board.brief(t["feature"]),
                "contract": self.board.contract(t["feature"]),
                "depends_on": self.board.dependency_results(t["id"])}

    @_safe
    def task_comment(self, text: str) -> dict:
        self.board.comment(self._own()["id"], text, self.author)
        return {"ok": True}

    @_safe
    def task_block(self, reason: str) -> dict:
        self.board.block(self._own()["id"], reason, self.author)
        return {"ok": True, "message": "blocked; stop working on this task now"}

    @_safe
    def task_propose_contract(self, change: str, reason: str) -> dict:
        pid = self.board.propose(self._own()["id"], change, reason, self.author)
        return {"proposal": pid, "message": "a human will decide; continue with parts the change doesn't affect"}

    @_safe
    def task_complete(self, summary: str) -> dict:
        self.board.complete(self._own()["id"], summary, self.author)
        return {"ok": True}

    # -- both modes ----------------------------------------------------------
    @_safe
    def board_read(self, feature: str | None = None, since_event: int = 0) -> dict:
        if self.task_id:
            t = self._own()
            feature = t["feature"]
            self.board.mark_seen(t["id"])
        if not feature:
            raise AosError("feature is required")
        return {"events": self.board.events(feature, since_event),
                "contract_version": self.board.feature(feature)["contract_version"]}

    @_safe
    def task_create(self, project: str, title: str, spec: str = "", depends_on: list[int] | None = None,
                    feature: str | None = None) -> dict:
        if self.task_id:
            feature = self._own()["feature"]
        if not feature:
            raise AosError("feature is required")
        return self._new_task(feature, project, title, spec, depends_on)

    # -- planner mode --------------------------------------------------------
    @_safe
    def feature_create(self, slug: str, title: str, brief: str = "", contract: str = "") -> dict:
        return self.board.create_feature(slug, title, brief, contract, author=self.author)

    @_safe
    def feature_show(self, slug: str) -> dict:
        return {"feature": self.board.feature(slug), "brief": self.board.brief(slug),
                "contract": self.board.contract(slug), "tasks": self.board.tasks(feature=slug),
                "pending_proposals": self.board.proposals(slug, "pending")}
```

Change `build_server(tools: Tools)` to `build_server(tools: Tools, board_tools: BoardTools | None = None)`. Before `return mcp`, add:
```python
    if board_tools is not None:
        _register_board_tools(mcp, board_tools)
```

Add after `build_server`:
```python
def _register_board_tools(mcp, bt: BoardTools) -> None:
    if bt.task_id:
        @mcp.tool()
        def task_show() -> dict:
            """Your board task: spec, feature brief, current contract and the results of tasks you depend on. Call first."""
            return bt.task_show()

        @mcp.tool()
        def board_read(since_event: int = 0) -> dict:
            """The feature timeline (other workers' progress, contract changes). Call before each step and before finishing."""
            return bt.board_read(since_event=since_event)

        @mcp.tool()
        def task_comment(text: str) -> dict:
            """Post progress or a note for other workers and the human."""
            return bt.task_comment(text)

        @mcp.tool()
        def task_block(reason: str) -> dict:
            """Stop: you cannot continue without a human. Explain exactly what is needed."""
            return bt.task_block(reason)

        @mcp.tool()
        def task_propose_contract(change: str, reason: str) -> dict:
            """Propose a change to the shared contract between projects; keep working on unaffected parts."""
            return bt.task_propose_contract(change, reason)

        @mcp.tool()
        def task_create(project: str, title: str, spec: str = "", depends_on: list[int] | None = None) -> dict:
            """Add a follow-up task to this feature (any linked project)."""
            return bt.task_create(project, title, spec, depends_on)

        @mcp.tool()
        def task_complete(summary: str) -> dict:
            """Finish your task: what changed, commits, how it was tested, what dependent tasks need to know."""
            return bt.task_complete(summary)
    else:
        @mcp.tool()
        def feature_create(slug: str, title: str, brief: str = "", contract: str = "") -> dict:
            """Create a multi-project feature with its brief and the shared contract (APIs, events, schemas)."""
            return bt.feature_create(slug, title, brief, contract)

        @mcp.tool()
        def feature_show(slug: str) -> dict:
            """A feature's brief, contract, tasks and pending contract proposals."""
            return bt.feature_show(slug)

        @mcp.tool()
        def task_create(project: str, title: str, feature: str, spec: str = "",
                        depends_on: list[int] | None = None) -> dict:
            """Add a task for one project to a feature; depends_on lists task ids that must finish first."""
            return bt.task_create(project, title, spec, depends_on, feature=feature)

        @mcp.tool()
        def board_read(feature: str, since_event: int = 0) -> dict:
            """A feature's timeline."""
            return bt.board_read(feature=feature, since_event=since_event)
```

Replace `run_server` with:
```python
def run_server(project: str | Path, task: int | None = None) -> None:
    from .link import sync

    cfg = load_user_config()
    repo = repo_root(cfg)
    project = Path(project).expanduser().resolve()
    slug = slug_for_path(project, cfg)

    def resync() -> None:
        if slug:
            sync(project)

    aos = AOS(repo, slug=slug)
    tools = Tools(aos, on_skills_changed=resync)
    board_tools = BoardTools(aos, task_id=task)
    cwd = os.getcwd()
    os.chdir(Path.home())  # FastMCP reads .env from cwd; keep project env files out of it
    try:
        mcp = build_server(tools, board_tools)
    finally:
        os.chdir(cwd)
    mcp.run()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mcp_board.py tests/test_mcp.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add aos/mcp_server.py tests/test_mcp_board.py
git commit -m "feat(board): MCP board tools for workers and planners"
```

---

### Task 7: CLI (`feature`, `task`, `board`, `serve --task`, doctor)

**Files:**
- Modify: `aos/cli.py`
- Test: `tests/test_cli_board.py`

**Interfaces:**
- Consumes: `Board`, `Dispatcher`, `resolve_profile`, `profile_settings`, `run_server(project, task)`
- Produces:
  - `aos feature new|list|show|approve|reject|done|cancel`
  - `aos task add|list|show|retry|cancel|unblock`
  - `aos board [--feature F]` and `aos board run [--feature F] [--parallel N] [--once]`
  - `aos serve --task N`
  - doctor lines for `claude`, `kiro-cli`, `KIRO_API_KEY`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_board.py`:
```python
import sys
from pathlib import Path

import pytest
import yaml

from aos.board import Board
from aos.cli import main
from aos.config import load_user_config, save_user_config
from aos.core import AOS

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).parent / "fake_worker.py"


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def env(configured, data, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    (configured / "aos.yaml").write_text(yaml.safe_dump({
        "board": {"default_worker": "fake"},
        "workers": {"fake": {"adapter": "command", "timeout_min": 0.05,
                             "command": [sys.executable, str(FAKE), "{task_id}"]}}}))
    projects = {}
    for name in ("api", "web"):
        projects[name] = {"path": str(git_repo(tmp_path / name)), "targets": ["claude"]}
        AOS(configured).register_project(name)
    save_user_config({**load_user_config(), "projects": projects})
    return configured, Board(data)


def test_feature_and_task_flow(env, capsys):
    _, board = env
    code, out, _ = run(["feature", "new", "checkout", "--title", "Checkout v2", "--projects", "api,web"], capsys)
    assert code == 0 and "brief.md" in out
    assert "- api" in board.brief("checkout")
    assert run(["feature", "new", "x", "--title", "X", "--projects", "ghost"], capsys)[0] == 1
    code, out, _ = run(["task", "add", "checkout", "api", "Orders endpoint"], capsys)
    assert code == 0 and "#1" in out
    assert run(["task", "add", "checkout", "web", "Use it", "--after", "1"], capsys)[0] == 0
    assert board.task(2)["depends_on"] == [1] and board.task(2)["worker"] == "fake"
    code, out, _ = run(["feature", "list"], capsys)
    assert "checkout" in out
    code, out, _ = run(["board"], capsys)
    assert "Orders endpoint" in out and "todo" in out
    code, out, _ = run(["board", "run", "--once"], capsys)
    assert code == 0 and board.task(1)["status"] == "done"
    code, out, _ = run(["task", "show", "1", "--log"], capsys)
    assert "done by fake worker" in out
    code, out, _ = run(["task", "list", "--status", "ready"], capsys)
    assert "Use it" in out


def test_proposals_and_task_controls(env, capsys):
    _, board = env
    run(["feature", "new", "checkout", "--title", "Checkout"], capsys)
    run(["task", "add", "checkout", "api", "Orders"], capsys)
    p = board.propose(1, "add currency", "multi-currency", author="task:1")
    code, out, _ = run(["feature", "show", "checkout"], capsys)
    assert f"#{p}" in out and "add currency" in out
    assert run(["feature", "approve", str(p)], capsys)[0] == 0
    assert board.contract("checkout")["version"] == 2
    p2 = board.propose(1, "drop", "no", author="task:1")
    assert run(["feature", "reject", str(p2), "--reason", "keep"], capsys)[0] == 0
    assert run(["task", "cancel", "1"], capsys)[0] == 0
    assert run(["task", "retry", "1", "--note", "again"], capsys)[0] == 0
    assert board.task(1)["status"] == "ready"
    board.block(1, "need key", author="task:1")
    code, out, _ = run(["board"], capsys)
    assert "need key" in out
    assert run(["task", "unblock", "1", "--note", "key in vault"], capsys)[0] == 0
    assert run(["feature", "done", "checkout"], capsys)[0] == 0
    assert run(["task", "show", "99"], capsys)[0] == 1


def test_board_shows_stuck_tasks(env, capsys):
    _, board = env
    run(["feature", "new", "checkout", "--title", "Checkout"], capsys)
    run(["task", "add", "checkout", "api", "a"], capsys)
    run(["task", "add", "checkout", "web", "b", "--after", "1"], capsys)
    run(["task", "cancel", "1"], capsys)
    code, out, _ = run(["board"], capsys)
    assert "waiting on failed/cancelled #1" in out


def test_doctor_reports_worker_clis(configured, capsys, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: None)
    code, out, _ = run(["doctor"], capsys)
    assert "claude" in out and "kiro-cli" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_board.py -q`
Expected: FAIL. `argparse` exits with "invalid choice: 'feature'", raising SystemExit 2.

- [ ] **Step 3: Implement**

In `aos/cli.py`, add `import os` and these imports:
```python
from .board import Board
```

Add the command functions after `cmd_doctor`:
```python
def _board_ctx():
    aos = AOS(repo_root())
    return aos, Board(aos.data, aos.settings["board"]["max_tasks_per_feature"])


def _check_projects(aos: AOS, names: list[str]) -> None:
    known = aos.projects()
    missing = [n for n in names if n not in known]
    if missing:
        raise AosError(f"unknown project(s): {', '.join(missing)}", "link them first with aos link")


def cmd_feature(a) -> int:
    aos, board = _board_ctx()
    if a.action == "new":
        projects = [p.strip() for p in (a.projects or "").split(",") if p.strip()]
        _check_projects(aos, projects)
        brief = f"# {a.title}\n\n## Goal\n\n## Projects\n" + "".join(f"- {p}\n" for p in projects)
        contract = "# Contract\n\nAPIs, events and schemas shared between the projects.\n"
        board.create_feature(a.slug, a.title, brief, contract)
        d = board.feature_dir(a.slug)
        print(f"feature {a.slug} created\n  edit: {d / 'brief.md'}\n  edit: {d / 'contract.md'}")
        print(f"then: aos task add {a.slug} <project> \"<title>\" [--after ids]   or use the aos-plan-feature skill")
    elif a.action == "list":
        for f in board.features():
            n = len(board.tasks(feature=f["slug"]))
            print(f"{f['slug']:<24} {f['status']:<10} contract v{f['contract_version']}  {n} task(s)  {f['title']}")
    elif a.action == "show":
        f = board.feature(a.slug)
        print(f"# {f['slug']}: {f['title']} [{f['status']}]\n")
        print(board.brief(a.slug).strip() or "(no brief)")
        c = board.contract(a.slug)
        print(f"\n## Contract v{c['version']}\n\n{c['text'].strip() or '(empty)'}\n")
        for t in board.tasks(feature=a.slug):
            print(f"#{t['id']:<4} {t['project']:<14} {t['status']:<10} {t['title']}")
        for p in board.proposals(a.slug, "pending"):
            print(f"\nproposal #{p['id']} (task #{p['task']}): {p['reason']}\n{p['body']}")
    elif a.action == "approve":
        new = None
        if a.edit:
            p = next((x for x in board.proposals(status="pending") if x["id"] == int(a.id)), None)
            if not p:
                raise AosError(f"no pending proposal #{a.id}")
            current = board.contract(p["feature"])["text"].rstrip()
            draft = f"{current}\n\n## Change (proposal #{p['id']})\n\n{p['body']}\n"
            tmp = board.data / f".proposal-{a.id}.md"
            tmp.write_text(draft)
            subprocess.call([os.environ.get("EDITOR", "vi"), str(tmp)])
            new = tmp.read_text()
            tmp.unlink()
        r = board.approve(int(a.id), new_contract=new)
        print(f"approved #{a.id}: contract is now v{r['contract_version']}")
    elif a.action == "reject":
        board.reject(int(a.id), a.reason or "")
        print(f"rejected #{a.id}")
    else:
        board.set_feature_status(a.slug, "done" if a.action == "done" else "cancelled")
        print(f"feature {a.slug} {a.action}")
    return 0


def cmd_task(a) -> int:
    from .workers.common import profile_settings, resolve_profile
    aos, board = _board_ctx()
    if a.action == "add":
        _check_projects(aos, [a.project])
        profile = resolve_profile(aos, a.project, a.worker)
        attempts = profile_settings(aos, profile, a.project)["max_attempts"]
        spec = Path(a.spec_file).read_text() if a.spec_file else ""
        deps = [int(x) for x in (a.after or "").split(",") if x.strip()]
        tid = board.add_task(a.feature, a.project, a.title, spec, deps, worker=profile, max_attempts=attempts)
        print(f"task #{tid} added ({a.project}, worker {profile})")
    elif a.action == "list":
        for t in board.tasks(feature=a.feature, status=a.status):
            print(f"#{t['id']:<4} {t['feature']:<18} {t['project']:<14} {t['status']:<10} {t['worker']:<7} {t['title']}")
    elif a.action == "show":
        t = board.task(int(a.id))
        print(yaml.safe_dump({k: t[k] for k in ("id", "feature", "project", "title", "status", "worker",
                                                 "attempts", "max_attempts", "depends_on", "result", "note")},
                             sort_keys=False, allow_unicode=True), end="")
        if t["spec"]:
            print(f"spec:\n{t['spec']}")
        if a.log:
            logs = sorted((board.data / "logs").glob(f"{t['id']}-*.log"), key=lambda p: p.stat().st_mtime)
            if logs:
                print(f"--- {logs[-1]} (last 60 lines)")
                print("\n".join(logs[-1].read_text(errors="replace").splitlines()[-60:]))
            else:
                print("(no log yet)")
    elif a.action == "retry":
        board.retry(int(a.id), note=a.note, worker=a.worker, resume=a.resume)
        print(f"task #{a.id} queued again")
    elif a.action == "cancel":
        board.cancel(int(a.id))
        print(f"task #{a.id} cancelled")
    else:
        board.unblock(int(a.id), a.note or "")
        print(f"task #{a.id} unblocked")
    return 0


def cmd_board(a) -> int:
    if a.action == "run":
        from .dispatcher import Dispatcher
        repo = repo_root()
        print("dispatching (Ctrl-C to stop launching; twice to stop workers)" if not a.once else "dispatching once")
        Dispatcher(repo, feature=a.feature, parallel=a.parallel).run(once=a.once)
        return 0
    _, board = _board_ctx()
    stuck = board.stuck()
    for f in board.features():
        if a.feature and f["slug"] != a.feature:
            continue
        print(f"{f['slug']} [{f['status']}] contract v{f['contract_version']}: {f['title']}")
        for t in board.tasks(feature=f["slug"]):
            extra = ""
            if t["id"] in stuck:
                extra = "  waiting on failed/cancelled " + ", ".join(f"#{d}" for d in stuck[t["id"]])
            elif t["status"] == "blocked":
                blk = [e for e in board.events(f["slug"]) if e["task"] == t["id"] and e["kind"] == "blocker"]
                extra = f"  {blk[-1]['body'][:80]}" if blk else ""
            print(f"  #{t['id']:<4} {t['project']:<14} {t['status']:<10} {t['attempts']}/{t['max_attempts']}  {t['title']}{extra}")
        for p in board.proposals(f["slug"], "pending"):
            print(f"  proposal #{p['id']} pending: {p['reason']}  (aos feature approve|reject {p['id']})")
    return 0
```

In `cmd_serve`, change `run_server(a.project)` to `run_server(a.project, a.task)`.

In `cmd_doctor`, before the final `return 0`, add:
```python
    line(shutil.which("claude") is not None,
         "claude CLI (board workers)" if shutil.which("claude") else "claude CLI not found (needed for claude workers)")
    kiro_cli = shutil.which("kiro-cli")
    line(bool(kiro_cli), "kiro-cli (board workers)" if kiro_cli else "kiro-cli not found (needed only for kiro workers)")
    if kiro_cli:
        line(bool(os.environ.get("KIRO_API_KEY")), "KIRO_API_KEY set" if os.environ.get("KIRO_API_KEY")
             else "KIRO_API_KEY not set (headless Kiro needs it unless you are logged in)")
```

In `_parser()`, replace the `("context", …), ("serve", …)` loop with:
```python
    s = sub.add_parser("context", help="print session context (SessionStart hook)")
    s.add_argument("--project", default=".")
    s.set_defaults(fn=cmd_context)

    s = sub.add_parser("serve", help="run the MCP server over stdio")
    s.add_argument("--project", default=".")
    s.add_argument("--task", type=int, help="board worker mode for this task id")
    s.set_defaults(fn=cmd_serve)
```
Then add before `sub.add_parser("status", …)`:
```python
    s = sub.add_parser("feature", help="multi-project features")
    fsub = s.add_subparsers(dest="action", required=True)
    f = fsub.add_parser("new")
    f.add_argument("slug")
    f.add_argument("--title", required=True)
    f.add_argument("--projects", help="comma-separated linked project slugs")
    fsub.add_parser("list")
    for name in ("show", "done", "cancel"):
        fsub.add_parser(name).add_argument("slug")
    f = fsub.add_parser("approve")
    f.add_argument("id")
    f.add_argument("--edit", action="store_true", help="edit the new contract in $EDITOR")
    f = fsub.add_parser("reject")
    f.add_argument("id")
    f.add_argument("--reason")
    s.set_defaults(fn=cmd_feature)

    s = sub.add_parser("task", help="board tasks")
    tsub = s.add_subparsers(dest="action", required=True)
    t = tsub.add_parser("add")
    t.add_argument("feature")
    t.add_argument("project")
    t.add_argument("title")
    t.add_argument("--spec-file")
    t.add_argument("--after", help="comma-separated task ids this task waits for")
    t.add_argument("--worker", choices=None, help="worker profile (claude, kiro, ...)")
    t = tsub.add_parser("list")
    t.add_argument("--feature")
    t.add_argument("--status")
    t = tsub.add_parser("show")
    t.add_argument("id")
    t.add_argument("--log", action="store_true")
    t = tsub.add_parser("retry")
    t.add_argument("id")
    t.add_argument("--resume", action="store_true")
    t.add_argument("--note")
    t.add_argument("--worker")
    tsub.add_parser("cancel").add_argument("id")
    t = tsub.add_parser("unblock")
    t.add_argument("id")
    t.add_argument("--note")
    s.set_defaults(fn=cmd_task)

    s = sub.add_parser("board", help="board status, or `board run` to dispatch workers")
    s.add_argument("action", nargs="?", choices=["run"])
    s.add_argument("--feature")
    s.add_argument("--parallel", type=int)
    s.add_argument("--once", action="store_true")
    s.set_defaults(fn=cmd_board)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli_board.py tests/test_cli.py -q`
Expected: all PASS

- [ ] **Step 5: Run the whole suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS

```bash
git add aos/cli.py tests/test_cli_board.py
git commit -m "feat(board): feature, task and board CLI commands"
```

---

### Task 8: Planning skill and README

**Files:**
- Create: `skills/aos-plan-feature/SKILL.md`
- Modify: `README.md`
- Test: `tests/test_seed.py` (extend)

**Interfaces:**
- Consumes: seed validation (`skills.validate`)

- [ ] **Step 1: Write the failing test**

In `tests/test_seed.py`, change the first assertion of `test_seed_skills_valid` to:
```python
    assert {"aos-memory", "aos-onboard-project", "aos-plan-feature"} <= set(names)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_seed.py -q`
Expected: FAIL (`aos-plan-feature` missing)

- [ ] **Step 3: Write the skill and README section**

`skills/aos-plan-feature/SKILL.md`:
```markdown
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
```

In `README.md`, insert before `## Layout`:
````markdown
## 5. Multi-project features (board)

One feature, several projects, several agents in parallel. Each agent works in its own project, knows the shared feature context, and coordinates through a local board.

```bash
# 1. Plan. Interactively: ask the agent to "plan a feature" (aos-plan-feature skill),
#    or by hand:
aos feature new checkout-v2 --title "Checkout v2" --projects shop-api,web,billing
$EDITOR ~/.agenticos/data/features/checkout-v2/brief.md      # goal, scope
$EDITOR ~/.agenticos/data/features/checkout-v2/contract.md   # APIs/events shared between projects
aos task add checkout-v2 shop-api "Orders endpoint"
aos task add checkout-v2 web "Checkout UI" --after 1
aos task add checkout-v2 billing "Invoice on order" --after 1 --worker kiro

# 2. Run: workers start in parallel (one per project at a time), each in its own git worktree
aos board run --feature checkout-v2

# 3. Watch and steer (another terminal)
aos board                                   # status, blockers, stuck tasks, pending proposals
aos task show 2 --log                       # what a worker did
aos feature approve 4 | reject 4            # contract change proposed by a worker
aos task unblock 3 --note "key is in vault" # answer a blocker
aos task retry 2 --resume --note "rename the button"   # feedback on a finished task
```

**What each worker gets.**
- *General:* SOUL, rules, org memory, the feature brief, the current contract, and the latest timeline.
- *Specific:* its project's memory, its task spec, the results of the tasks it depends on, and graphskill.
- *Tools:* `task_show`, `board_read`, `task_comment`, `task_block`, `task_propose_contract`, `task_create`, `task_complete`.

**Where the work lands.** `~/.agenticos/worktrees/<feature>/<project>` on branch `feature/<feature>`. Your own checkouts are never touched, and nothing is pushed. Review the branches, then merge or open PRs yourself.

**Claude Code or Kiro.** Each task runs on a worker profile:
1. `--worker` on the task;
2. `projects.<slug>.worker.profile` in `~/.agenticos/data/projects.yaml`;
3. `board.default_worker` in `aos.yaml`.

Kiro workers need `kiro-cli` with `KIRO_API_KEY` (or a login). Allowed tools per project:
```yaml
# ~/.agenticos/data/projects.yaml
projects:
  shop-api:
    worker: {profile: claude, allowed_tools: ["shell:npm test", "shell:npm run lint"]}
```
Workers never get "allow everything": only the listed tools, plus file edits inside their worktree.

**Data.** The board is one SQLite file, `~/.agenticos/data/board.db` (no server). Feature files, logs and run contexts sit next to it. All of it is local.
````

Also add a row to the "Today vs. next" table in section 4:
```markdown
| Multi-project features with parallel Claude Code / Kiro workers (board) | ✅ works now |
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add skills/aos-plan-feature README.md tests/test_seed.py
git commit -m "docs(board): aos-plan-feature skill and README guide"
```

---

### Task 9: Real end-to-end verification

**Files:** none (verification only; fix and commit anything it reveals)

- [ ] **Step 1: Two scratch projects with a dependency**

```bash
S=<scratchpad>/board-e2e && rm -rf $S && mkdir -p $S
for p in e2e-api e2e-web; do
  mkdir $S/$p && git -C $S/$p init -q -b main && echo "# $p" > $S/$p/README.md \
  && git -C $S/$p add . && git -C $S/$p commit -qm init && aos link $S/$p --name $p --no-graphskill
done
aos feature new e2e --title "E2E greeting" --projects e2e-api,e2e-web
printf 'GET /greeting returns JSON {"text": string}\n' >> ~/.agenticos/data/features/e2e/contract.md
aos task add e2e e2e-api "Add greeting.py exposing greeting() -> {'text': 'hello'} with a unit test"
aos task add e2e e2e-web "Add ui.py that renders the greeting text in <h1>, per the contract" --after 1
```

- [ ] **Step 2: Run with real Claude Code workers**

```bash
aos board run --feature e2e --once && aos board --feature e2e && aos board run --feature e2e --once && aos board --feature e2e
```
Expected: task 1 is `done`, then task 2 is `done`. Each worktree has commits on `feature/e2e`, the scratch checkouts are unchanged on `main`, and `aos task show 2` shows a result that refers to task 1's result. If a worker fails, read `aos task show N --log` and fix the cause (systematic-debugging).

- [ ] **Step 3: Clean up**

```bash
for p in e2e-api e2e-web; do aos unlink $S/$p; git -C $S/$p worktree remove --force ~/.agenticos/worktrees/e2e/$p; done
aos feature cancel e2e
```
Remove the `e2e-*` entries and `features/e2e` from `~/.agenticos/data` (`projects.yaml`, `memory/projects/e2e-*.md`, `features/e2e`) so no test data remains. The board rows stay; they're marked cancelled.

- [ ] **Step 4: Kiro**

Kiro (no `kiro-cli` on this machine) is checked by the user on another computer (2026-10-02), using the checklist from the conversation. Record the outcome in the ledger as "Kiro e2e: pending user verification".
