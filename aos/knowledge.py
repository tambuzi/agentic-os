"""Full-text search over knowledge/, memory/ and skills/ (SQLite FTS5).

The index is derived state in $AOS_HOME/aos.db and is rebuilt whenever the
set of source files or any mtime changes. Cheap at team-repo scale.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path

from .errors import AosError
from .skills import split_frontmatter
from .store import inside

PATTERNS = ("knowledge/**/*.md", "memory/**/*.md", "skills/*/SKILL.md")


def _title(rel: str, text: str) -> str:
    if text.startswith("---\n"):
        try:
            meta, _ = split_frontmatter(text)
            if meta.get("title") or meta.get("name"):
                return str(meta.get("title") or meta.get("name"))
        except AosError:
            pass
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return Path(rel).stem


class KnowledgeIndex:
    def __init__(self, repo: str | Path, db_path: str | Path):
        self.repo = Path(repo).resolve()
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.db_path)
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(path UNINDEXED, title, body)")
        c.execute("CREATE TABLE IF NOT EXISTS doc_files(path TEXT PRIMARY KEY, mtime REAL)")
        return c

    def _sources(self) -> dict[str, float]:
        out = {}
        for pattern in PATTERNS:
            for p in self.repo.glob(pattern):
                rel = p.relative_to(self.repo)
                if any(part.startswith(".") for part in rel.parts):
                    continue
                out[rel.as_posix()] = p.stat().st_mtime
        return out

    def refresh(self) -> bool:
        current = self._sources()
        with closing(self._connect()) as c, c:
            stored = dict(c.execute("SELECT path, mtime FROM doc_files"))
            if stored == current:
                return False
            c.execute("DELETE FROM docs")
            c.execute("DELETE FROM doc_files")
            for rel, mtime in current.items():
                text = (self.repo / rel).read_text(encoding="utf-8", errors="replace")
                c.execute("INSERT INTO docs VALUES (?, ?, ?)", (rel, _title(rel, text), text))
                c.execute("INSERT INTO doc_files VALUES (?, ?)", (rel, mtime))
        return True

    def search(self, query: str, limit: int = 10) -> list[dict]:
        tokens = re.findall(r"\w+", (query or "").lower())
        if not tokens:
            return []
        match = " OR ".join(f'"{t}"' for t in tokens)
        self.refresh()
        with closing(self._connect()) as c:
            rows = c.execute(
                "SELECT path, title, snippet(docs, 2, '[', ']', '…', 16), bm25(docs) "
                "FROM docs WHERE docs MATCH ? ORDER BY bm25(docs) LIMIT ?",
                (match, max(1, min(int(limit), 50))),
            ).fetchall()
        return [{"path": p, "title": t, "snippet": s, "score": round(-r, 3)} for p, t, s, r in rows]

    def read(self, rel: str) -> dict:
        p = inside(self.repo, rel)
        if p.suffix != ".md" or not p.is_file():
            raise AosError(f"no markdown file at {rel}", "use a path returned by knowledge_search")
        return {"path": p.relative_to(self.repo).as_posix(), "content": p.read_text(encoding="utf-8")}
