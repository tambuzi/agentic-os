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
  contract_seen INTEGER, result TEXT, session_id TEXT, pid INTEGER, proc_start TEXT, note TEXT,
  generation INTEGER NOT NULL DEFAULT 0,
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
        cols = {r["name"] for r in c.execute("PRAGMA table_info(tasks)")}
        if "proc_start" not in cols:  # boards created before proc_start existed
            c.execute("ALTER TABLE tasks ADD COLUMN proc_start TEXT")
        if "generation" not in cols:  # boards created before attempt tokens existed
            c.execute("ALTER TABLE tasks ADD COLUMN generation INTEGER NOT NULL DEFAULT 0")
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
        check_slug(slug, "feature")
        return self.data / "features" / slug

    def create_feature(self, slug: str, title: str, brief: str = "", contract: str = "",
                       author: str = "human") -> dict:
        check_slug(slug, "feature")
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
            if status == "cancelled":  # the dispatcher terminates any of these still running
                for r in c.execute("SELECT id FROM tasks WHERE feature=? AND status NOT IN ('done','cancelled')",
                                   (slug,)).fetchall():
                    self._transition(c, r["id"], STATUSES, "cancelled", author, "feature cancelled")

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
                "SELECT t.id FROM tasks t JOIN features f ON f.slug=t.feature"
                " WHERE t.status='todo' AND f.status='open' AND NOT EXISTS ("
                " SELECT 1 FROM deps d JOIN tasks x ON x.id=d.depends_on"
                " WHERE d.task=t.id AND x.status!='done') ORDER BY t.id")]
            for tid in ids:
                self._transition(c, tid, ("todo",), "ready", "dispatcher", "dependencies done → ready")
        return ids

    def stuck(self) -> dict[int, list[int]]:
        """todo tasks that can never become ready: a dependency failed or was cancelled,
        directly or further up the chain. Maps task id -> the dependencies in that dead set."""
        out: dict[int, list[int]] = {}
        for r in self._rows(
                "WITH RECURSIVE dead(id) AS ("
                " SELECT id FROM tasks WHERE status IN ('failed','cancelled')"
                " UNION SELECT d.task FROM deps d JOIN dead ON d.depends_on=dead.id"
                " JOIN tasks t ON t.id=d.task WHERE t.status='todo')"
                " SELECT d.task, d.depends_on FROM deps d JOIN tasks t ON t.id=d.task"
                " WHERE t.status='todo' AND d.depends_on IN (SELECT id FROM dead)"
                " ORDER BY d.task, d.depends_on"):
            out.setdefault(r["task"], []).append(r["depends_on"])
        return out

    def dispatchable(self, feature: str | None = None) -> list[dict]:
        """ready tasks of open features, oldest first."""
        sql = ("SELECT t.* FROM tasks t JOIN features f ON f.slug=t.feature "
               "WHERE t.status='ready' AND f.status='open'")
        params: list = []
        if feature:
            sql, params = sql + " AND t.feature=?", [feature]
        return self._rows(sql + " ORDER BY t.id", params)

    # -- events --------------------------------------------------------------
    def events(self, feature: str, since: int = 0, limit: int | None = None) -> list[dict]:
        if limit:
            return self._rows("SELECT * FROM (SELECT * FROM events WHERE feature=? AND id>? "
                              "ORDER BY id DESC LIMIT ?) ORDER BY id", (feature, int(since), int(limit)))
        return self._rows("SELECT * FROM events WHERE feature=? AND id>? ORDER BY id",
                          (feature, int(since)))

    @staticmethod
    def _check_generation(t, generation: int | None) -> None:
        """Attempt token: refuse a worker bound to an older attempt (it outlived it)."""
        if generation is not None and t["generation"] != int(generation):
            raise AosError(f"stale worker: you belong to attempt {generation}, but task #{t['id']} is now on "
                           f"attempt {t['generation']} ({t['status']})",
                           "stop working on this task; another attempt owns it now")

    def comment(self, tid: int, text: str, author: str, generation: int | None = None) -> None:
        if not (text or "").strip():
            raise AosError("comment is empty")
        with self._tx() as c:
            t = self._task_row(c, tid)
            self._check_generation(t, generation)
            self._event(c, t["feature"], int(tid), "comment", author, text.strip())

    # -- task transitions ----------------------------------------------------
    def _version(self, c, feature: str) -> int:
        return c.execute("SELECT contract_version FROM features WHERE slug=?", (feature,)).fetchone()[0]

    def claim(self, tid: int) -> dict:
        with self._tx() as c:
            t = self._task_row(c, tid)
            self._transition(c, tid, ("ready",), "running", "dispatcher",
                             f"attempt {t['attempts'] + 1} started",
                             attempts=t["attempts"] + 1, generation=t["generation"] + 1,
                             contract_seen=self._version(c, t["feature"]), started=now(), pid=None)
        return self.task(tid)

    def set_process(self, tid: int, pid: int | None, session_id: str | None,
                    proc_start: str | None = None) -> None:
        with self._tx() as c:
            self._task_row(c, tid)
            c.execute("UPDATE tasks SET pid=?, proc_start=?, session_id=COALESCE(?, session_id), updated=? "
                      "WHERE id=?", (pid, proc_start, session_id, now(), int(tid)))

    def mark_seen(self, tid: int) -> None:
        with self._tx() as c:
            t = self._task_row(c, tid)
            c.execute("UPDATE tasks SET contract_seen=? WHERE id=?", (self._version(c, t["feature"]), int(tid)))

    def complete(self, tid: int, summary: str, author: str, generation: int | None = None) -> None:
        if not (summary or "").strip():
            raise AosError("summary is empty", "say what changed, the commits, and how it was tested")
        with self._tx() as c:
            t = self._task_row(c, tid)
            self._check_generation(t, generation)
            v = self._version(c, t["feature"])
            if t["status"] == "running" and (t["contract_seen"] or 0) < v:
                raise AosError(f"contract changed (v{t['contract_seen']}→v{v}) since you last read it",
                               "call board_read or task_show, re-check your work against the new "
                               "contract, then complete")
            self._transition(c, tid, ("running",), "done", author,
                             f"done: {summary.strip().splitlines()[0][:120]}",
                             result=summary.strip(), pid=None, note=None, resume=0)
            self._event(c, t["feature"], int(tid), "result", author, summary.strip())

    def block(self, tid: int, reason: str, author: str, generation: int | None = None) -> None:
        if not (reason or "").strip():
            raise AosError("block reason is empty")
        with self._tx() as c:
            self._check_generation(self._task_row(c, tid), generation)
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
    def propose(self, tid: int, body: str, reason: str, author: str, generation: int | None = None) -> int:
        if not (body or "").strip():
            raise AosError("proposal is empty")
        with self._tx() as c:
            t = self._task_row(c, tid)
            self._check_generation(t, generation)
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
