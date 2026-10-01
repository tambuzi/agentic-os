# agenticOS Core (sub-project 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `aos` Python package: a CLI and an MCP server that give Claude Code and Kiro a shared, capped memory, skills, knowledge search and per-session context injection across linked projects, with graphskill set up in every linked project.

**Architecture:** Pure modules (`store`, `config`, `memory`, `skills`, `inbox`, `knowledge`) under one facade (`core.AOS`) that applies write modes (direct vs. inbox). `context` builds the SessionStart text. `writer` + `adapters/{claude,kiro}` + `link` render project files with manifest-tracked ownership. `mcp_server` and `cli` are thin shells over `AOS`.

**Tech Stack:** Python ≥3.11 (dev venv uses `/opt/homebrew/bin/python3.13`), `mcp` (FastMCP), `pyyaml`, stdlib `sqlite3` FTS5, `pytest`.

**Spec:** `docs/superpowers/specs/2026-10-01-agenticos-design.md`

## Global Constraints

- Python `>=3.11`. Runtime deps: only `mcp>=1.2` and `pyyaml>=6`.
- All state outside the repo lives under `$AOS_HOME` (default `~/.agenticos`). The repo can be overridden with `$AOS_REPO`.
- Memory caps (chars): org 4000, project 2200, user 1375. Default modes: org `inbox`, project `direct`, user `direct`, skills `direct`.
- Context budget default: `context.max_chars: 16000`.
- Memory entry separator: a line containing only `§`.
- `aos context` always exits 0. Errors print one line starting `[aos]`.
- MCP tools never raise. Errors return `{"error": ..., "hint": ...}`.
- File writes are atomic (temp + `os.replace`). Shared files are written under an `fcntl` lock.
- Every agent-supplied path is resolved and must stay inside its root.
- Link never changes or removes a file or JSON key it does not own (owned = listed in `.aos/manifest.json`).
- Skills are copied, not symlinked. Each skill is `<name>/SKILL.md` with `name` + `description` frontmatter.
- **Deviation from spec §4.6, decided while planning:** MCP/hook commands use the absolute path of the `aos` executable (`shutil.which("aos")`, else `"aos"`). GUI-launched Kiro/Claude may not have `~/.local/bin` on PATH, and graphskill already writes absolute, per-user paths into `.mcp.json`.
- graphskill command: `config.yaml` key `graphskill_cmd` (list of argv) if set, else `["graphskill"]` if on PATH, else skip with a warning.

## Review Focus

1. A project with a malformed existing `.mcp.json` / `.claude/settings.local.json` should fail `aos link` with a message naming the file, before anything is written. (Task 7 `test_malformed_json_aborts_cleanly`)
2. A user's own `.claude/skills/<name>/` with the same name as an agenticOS skill must be left untouched and reported as a conflict. (Task 7 `test_user_skill_with_same_name_is_conflict`)
3. Knowledge queries containing FTS syntax (`-`, `"`, `AND`, `NEAR(`, `*`, empty) must not crash. (Task 5 `test_query_with_operators_and_punctuation`)
4. `aos context` with no config, an unlinked project, or a broken repo still exits 0 with a single `[aos]` line, so the session is never blocked. (Task 9 `test_context_never_fails`, `test_context_unlinked`)
5. Unlink after link must restore user-owned config exactly: user hooks, MCP servers, `.gitignore`, permissions. (Task 7 `test_preserves_user_config`)

---

## File Structure

```
pyproject.toml            package metadata, `aos` console script
.gitignore                repo ignores (venv, caches, lock files)
aos/__init__.py           version
aos/__main__.py           python -m aos
aos/errors.py             AosError(message, hint)
aos/store.py              read_text, write_atomic, locked, inside
aos/config.py             AOS_HOME, settings (aos.yaml), user config, slugs, repo_root
aos/memory.py             capped §-separated entry memory
aos/skills.py             skill CRUD + frontmatter validation + project filter
aos/inbox.py              staged proposals (yaml files in inbox/)
aos/core.py               AOS facade: scopes, modes, proposals, project registry
aos/knowledge.py          FTS5 index + search/read
aos/context.py            PROTOCOL text + build_context()
aos/writer.py             Writer (manifest-owned writes), LinkContext, copy_skills
aos/adapters/__init__.py
aos/adapters/claude.py    Claude Code render
aos/adapters/kiro.py      Kiro render
aos/link.py               link / sync / unlink / graphskill setup
aos/mcp_server.py         Tools (testable) + build_server + run_server
aos/cli.py                argparse CLI
SOUL.md AGENTS.md aos.yaml projects.yaml memory/ knowledge/ skills/ inbox/   seed content
tests/conftest.py + tests/test_*.py
```

---

### Task 1: Scaffold, errors, store, config

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `aos/__init__.py`, `aos/errors.py`, `aos/store.py`, `aos/config.py`
- Test: `tests/conftest.py`, `tests/test_store.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `AosError(message: str, hint: str = "")` with `.message`, `.hint`, `.to_dict() -> dict`
  - `read_text(path) -> str` (`""` if missing); `write_atomic(path, data: str | bytes) -> None`; `locked(path)` context manager; `inside(root, rel: str) -> Path` (raises AosError on escape)
  - `aos_home() -> Path`; `DEFAULTS: dict`; `load_settings(repo) -> dict`; `config_path() -> Path`; `load_user_config() -> dict` (always has `"projects"`); `save_user_config(cfg) -> None`; `repo_root(cfg=None) -> Path`; `slug_for_path(path, cfg=None) -> str | None`; `default_slug(path) -> str`; `check_slug(slug) -> None`; `graphskill_cmd(cfg=None) -> list[str] | None`
  - Fixtures: `home`, `repo`, `configured`, `project`, `make_skill(name, description=..., body=..., projects=None, files=None) -> Path`

- [ ] **Step 1: Create venv and scaffolding**

```bash
cd /Users/alejandrofernandez/Desktop/Projects/agenticOS
/opt/homebrew/bin/python3.13 -m venv .venv
```

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "agenticos"
version = "0.1.0"
description = "agenticOS: a shared, self-improving brain for Claude Code and Kiro across projects."
requires-python = ">=3.11"
dependencies = ["mcp>=1.2", "pyyaml>=6"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
aos = "aos.cli:main"

[tool.setuptools.packages.find]
include = ["aos*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.gitignore`:
```
.venv/
__pycache__/
*.egg-info/
build/
.pytest_cache/
.*.lock
```

`aos/__init__.py`:
```python
"""agenticOS: shared memory, skills and context for Claude Code and Kiro."""

__version__ = "0.1.0"
```

Run: `.venv/bin/pip install -q -e '.[dev]'` (if the editable console script misbehaves, tests still run via `.venv/bin/python -m pytest`).

- [ ] **Step 2: Write the failing tests**

`tests/conftest.py`:
```python
from pathlib import Path

import pytest
import yaml

from aos.config import save_user_config


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("AOS_HOME", str(h))
    monkeypatch.delenv("AOS_REPO", raising=False)
    return h


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "agenticOS"
    (r / "memory" / "projects").mkdir(parents=True)
    (r / "skills").mkdir()
    (r / "knowledge").mkdir()
    (r / "SOUL.md").write_text("You are the team agent.\n")
    (r / "AGENTS.md").write_text("# Org rules\n- Use UTC.\n")
    return r


@pytest.fixture
def configured(home, repo) -> Path:
    save_user_config({"repo": str(repo), "projects": {}})
    return repo


@pytest.fixture
def project(tmp_path) -> Path:
    p = tmp_path / "shop-api"
    p.mkdir()
    return p


@pytest.fixture
def make_skill(repo):
    def _make(name, description="Does a thing.", body="Steps.\n", projects=None, files=None) -> Path:
        meta = {"name": name, "description": description}
        if projects:
            meta["metadata"] = {"aos": {"projects": projects}}
        d = repo / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n\n" + body)
        for rel, text in (files or {}).items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(text)
        return d

    return _make
```

`tests/test_store.py`:
```python
import pytest

from aos.errors import AosError
from aos.store import inside, locked, read_text, write_atomic


def test_read_missing_returns_empty(tmp_path):
    assert read_text(tmp_path / "nope.md") == ""


def test_write_atomic_creates_parents_and_leaves_no_temp(tmp_path):
    p = tmp_path / "a" / "b.md"
    write_atomic(p, "hi")
    assert p.read_text() == "hi"
    assert [x.name for x in p.parent.iterdir()] == ["b.md"]


def test_write_atomic_bytes(tmp_path):
    p = tmp_path / "x.bin"
    write_atomic(p, b"\x00\x01")
    assert p.read_bytes() == b"\x00\x01"


def test_locked_allows_sequential_use(tmp_path):
    p = tmp_path / "m.md"
    with locked(p):
        write_atomic(p, "1")
    with locked(p):
        assert read_text(p) == "1"


def test_inside_rejects_escape(tmp_path):
    assert inside(tmp_path, "a/b.md") == (tmp_path / "a" / "b.md").resolve()
    with pytest.raises(AosError):
        inside(tmp_path, "../etc/passwd")
```

`tests/test_config.py`:
```python
import pytest

from aos.config import (
    check_slug, default_slug, graphskill_cmd, load_settings, repo_root,
    save_user_config, slug_for_path,
)
from aos.errors import AosError


def test_settings_defaults_and_override(repo):
    s = load_settings(repo)
    assert s["memory"]["org"]["mode"] == "inbox"
    assert s["memory"]["project"]["cap"] == 2200
    (repo / "aos.yaml").write_text("memory:\n  org:\n    mode: direct\n")
    s = load_settings(repo)
    assert s["memory"]["org"]["mode"] == "direct"
    assert s["memory"]["org"]["cap"] == 4000


def test_repo_root_requires_config(home):
    with pytest.raises(AosError) as e:
        repo_root()
    assert "aos init" in e.value.hint


def test_repo_root_from_config(configured):
    assert repo_root() == configured.resolve()


def test_repo_root_rejects_non_repo(home, tmp_path):
    save_user_config({"repo": str(tmp_path), "projects": {}})
    with pytest.raises(AosError):
        repo_root()


def test_slug_for_path_matches_subdirs(home, project):
    save_user_config({"repo": "x", "projects": {"shop": {"path": str(project), "targets": ["claude"]}}})
    (project / "src").mkdir()
    assert slug_for_path(project / "src") == "shop"
    assert slug_for_path(project.parent) is None


def test_default_slug(tmp_path):
    assert default_slug(tmp_path / "My Shop_API") == "my-shop-api"


def test_check_slug():
    check_slug("ok-1")
    with pytest.raises(AosError):
        check_slug("../x")


def test_graphskill_cmd(home, monkeypatch):
    monkeypatch.setattr("aos.config.shutil.which", lambda n: None)
    assert graphskill_cmd() is None
    save_user_config({"projects": {}, "graphskill_cmd": ["/x/python", "-m", "graphskill"]})
    assert graphskill_cmd() == ["/x/python", "-m", "graphskill"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py tests/test_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aos.config'`

- [ ] **Step 4: Implement**

`aos/errors.py`:
```python
class AosError(Exception):
    """A user-facing failure: a message plus an optional hint on how to fix it."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        d = {"error": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d
```

`aos/store.py`:
```python
"""Atomic, lockable file access. Every write in agenticOS goes through here."""

from __future__ import annotations

import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .errors import AosError


def read_text(path: str | Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write_atomic(path: str | Path, data: str | bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@contextmanager
def locked(path: str | Path):
    """Exclusive advisory lock on a sibling `.<name>.lock` file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(f".{path.name}.lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def inside(root: str | Path, rel: str) -> Path:
    """Resolve `rel` under `root`, refusing anything that escapes it."""
    base = Path(root).resolve()
    p = (base / rel).resolve()
    if p != base and base not in p.parents:
        raise AosError(f"path escapes {base.name}/: {rel}")
    return p
```

`aos/config.py`:
```python
"""Settings (agenticOS/aos.yaml), per-user config (~/.agenticos/config.yaml), slugs."""

from __future__ import annotations

import copy
import os
import re
import shutil
from pathlib import Path

import yaml

from .errors import AosError
from .store import read_text, write_atomic

DEFAULTS = {
    "memory": {
        "org": {"cap": 4000, "mode": "inbox"},
        "project": {"cap": 2200, "mode": "direct"},
        "user": {"cap": 1375, "mode": "direct"},
    },
    "skills": {"mode": "direct"},
    "context": {"max_chars": 16000},
}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def aos_home() -> Path:
    return Path(os.environ.get("AOS_HOME", Path.home() / ".agenticos")).expanduser()


def _deep_merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_settings(repo: str | Path) -> dict:
    data = yaml.safe_load(read_text(Path(repo) / "aos.yaml")) or {}
    return _deep_merge(copy.deepcopy(DEFAULTS), data)


def config_path() -> Path:
    return aos_home() / "config.yaml"


def load_user_config() -> dict:
    data = yaml.safe_load(read_text(config_path())) or {}
    data.setdefault("projects", {})
    return data


def save_user_config(cfg: dict) -> None:
    write_atomic(config_path(), yaml.safe_dump(cfg, sort_keys=True, allow_unicode=True))


def repo_root(cfg: dict | None = None) -> Path:
    raw = os.environ.get("AOS_REPO") or (cfg if cfg is not None else load_user_config()).get("repo")
    if not raw:
        raise AosError("agenticOS repo not configured", "run `aos init <path-to-agenticOS>`")
    p = Path(raw).expanduser().resolve()
    if not (p / "SOUL.md").exists():
        raise AosError(f"not an agenticOS repo: {p}", "expected SOUL.md at its root")
    return p


def slug_for_path(path: str | Path, cfg: dict | None = None) -> str | None:
    """Slug of the linked project containing `path` (the path itself or a parent)."""
    target = Path(path).expanduser().resolve()
    projects = (cfg if cfg is not None else load_user_config())["projects"]
    roots = {Path(info["path"]).resolve(): slug for slug, info in projects.items()}
    for candidate in (target, *target.parents):
        if candidate in roots:
            return roots[candidate]
    return None


def default_slug(path: str | Path) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", Path(path).resolve().name.lower()).strip("-")
    return s or "project"


def check_slug(slug: str) -> None:
    if not SLUG_RE.match(slug or ""):
        raise AosError(f"invalid project slug {slug!r}", "lowercase a-z, 0-9 and '-', max 64 chars")


def graphskill_cmd(cfg: dict | None = None) -> list[str] | None:
    cmd = (cfg if cfg is not None else load_user_config()).get("graphskill_cmd")
    if cmd:
        return list(cmd)
    exe = shutil.which("graphskill")
    return [exe] if exe else None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store.py tests/test_config.py -q`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore aos tests
git commit -m "feat: scaffold aos package with store and config"
```

---

### Task 2: Capped memory

**Files:**
- Create: `aos/memory.py`
- Test: `tests/test_memory.py`

**Interfaces:**
- Consumes: `read_text`, `write_atomic`, `locked`, `AosError`
- Produces: `parse(text) -> list[str]`, `render(entries) -> str`, `Memory(path, cap)` with `.entries() -> list[str]`, `.snapshot() -> {"entries", "used", "cap"}`, `.add(text)`, `.replace(old, new)`, `.remove(old)` (each returns a snapshot)

- [ ] **Step 1: Write the failing test**

`tests/test_memory.py`:
```python
import pytest

from aos.errors import AosError
from aos.memory import Memory, parse, render


def mem(tmp_path, cap=200):
    return Memory(tmp_path / "m.md", cap)


def test_add_and_read(tmp_path):
    m = mem(tmp_path)
    m.add("Prices are in EUR.")
    snap = m.add("Deploys on Tuesdays.\nNever Fridays.")
    assert snap["entries"] == ["Prices are in EUR.", "Deploys on Tuesdays.\nNever Fridays."]
    assert snap["used"] == len((tmp_path / "m.md").read_text())
    assert snap["cap"] == 200


def test_parse_roundtrip():
    text = "a\n§\nb\nc\n"
    assert parse(text) == ["a", "b\nc"]
    assert render(parse(text)) == text


def test_add_duplicate_is_noop(tmp_path):
    m = mem(tmp_path)
    m.add("x")
    assert m.add("x")["entries"] == ["x"]


def test_cap_overflow_errors_without_writing(tmp_path):
    m = mem(tmp_path, cap=20)
    m.add("0123456789")
    with pytest.raises(AosError) as e:
        m.add("abcdefghijklmnop")
    assert "consolidate" in e.value.hint
    assert m.entries() == ["0123456789"]


def test_replace_requires_unique_match(tmp_path):
    m = mem(tmp_path)
    m.add("deploy staging")
    m.add("deploy prod")
    with pytest.raises(AosError) as e:
        m.replace("deploy", "x")
    assert "2 entries" in e.value.message
    m.replace("prod", "deploy prod via CI")
    assert m.entries() == ["deploy staging", "deploy prod via CI"]


def test_remove_and_missing(tmp_path):
    m = mem(tmp_path)
    m.add("a1")
    m.remove("a1")
    assert m.entries() == []
    with pytest.raises(AosError):
        m.remove("zzz")


def test_rejects_separator_and_empty(tmp_path):
    m = mem(tmp_path)
    with pytest.raises(AosError):
        m.add("a\n§\nb")
    with pytest.raises(AosError):
        m.add("   ")


def test_section_sign_inline_is_fine(tmp_path):
    m = mem(tmp_path)
    m.add("see § 4.1")
    assert m.entries() == ["see § 4.1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_memory.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aos.memory'`

- [ ] **Step 3: Implement**

`aos/memory.py`:
```python
"""Small, curated, always-in-context memory (hermes MEMORY.md style).

A memory file is a list of entries separated by lines containing only `§`.
Writes that would exceed the cap fail loudly so the agent consolidates
instead of silently losing entries.
"""

from __future__ import annotations

from pathlib import Path

from .errors import AosError
from .store import locked, read_text, write_atomic

SEP = "§"


def parse(text: str) -> list[str]:
    entries, cur = [], []
    for line in text.splitlines():
        if line.strip() == SEP:
            if "\n".join(cur).strip():
                entries.append("\n".join(cur).strip())
            cur = []
        else:
            cur.append(line)
    if "\n".join(cur).strip():
        entries.append("\n".join(cur).strip())
    return entries


def render(entries: list[str]) -> str:
    return f"\n{SEP}\n".join(entries) + "\n" if entries else ""


def _clean(text: str) -> str:
    t = (text or "").strip()
    if not t:
        raise AosError("memory entry is empty")
    if any(line.strip() == SEP for line in t.splitlines()):
        raise AosError(f"memory entry may not contain a line with only '{SEP}'")
    return t


class Memory:
    def __init__(self, path: str | Path, cap: int):
        self.path = Path(path)
        self.cap = cap

    def entries(self) -> list[str]:
        return parse(read_text(self.path))

    def snapshot(self) -> dict:
        es = self.entries()
        return {"entries": es, "used": len(render(es)), "cap": self.cap}

    def _save(self, entries: list[str]) -> None:
        size = len(render(entries))
        if size > self.cap:
            raise AosError(
                f"memory full: change would use {size}/{self.cap} chars",
                "consolidate or remove entries (memory_replace / memory_remove), then retry",
            )
        write_atomic(self.path, render(entries))

    @staticmethod
    def _match(entries: list[str], old: str) -> int:
        if not (old or "").strip():
            raise AosError("match text is empty")
        hits = [i for i, e in enumerate(entries) if old in e]
        if not hits:
            raise AosError(f"no entry contains {old!r}", "call memory_read to see current entries")
        if len(hits) > 1:
            candidates = " | ".join(entries[i][:80] for i in hits)
            raise AosError(f"{len(hits)} entries contain {old!r}", f"use a longer unique substring; candidates: {candidates}")
        return hits[0]

    def add(self, text: str) -> dict:
        text = _clean(text)
        with locked(self.path):
            es = self.entries()
            if text not in es:
                self._save(es + [text])
        return self.snapshot()

    def replace(self, old: str, new: str) -> dict:
        new = _clean(new)
        with locked(self.path):
            es = self.entries()
            es[self._match(es, old)] = new
            self._save(es)
        return self.snapshot()

    def remove(self, old: str) -> dict:
        with locked(self.path):
            es = self.entries()
            del es[self._match(es, old)]
            self._save(es)
        return self.snapshot()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_memory.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add aos/memory.py tests/test_memory.py
git commit -m "feat: capped entry-based memory"
```

---

### Task 3: Skills

**Files:**
- Create: `aos/skills.py`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `inside`, `write_atomic`, `AosError`
- Produces: `NAME_RE`, `check_name(name)`, `split_frontmatter(text) -> (dict, body)`, `join_frontmatter(meta, body) -> str`, `validate(name, text) -> dict`, `list_skills(repo) -> list[{"name","description","projects"}]`, `skills_for(repo, slug) -> list[str]`, `view(repo, name) -> {"name","content","files"}`, `create(repo, name, content, created_by="agent") -> dict`, `patch(repo, name, old, new) -> dict`, `write_file(repo, name, rel, content) -> dict`, `delete(repo, name) -> dict` (archives to `skills/.archive/<name>`)

- [ ] **Step 1: Write the failing test**

`tests/test_skills.py`:
```python
import pytest

from aos import skills
from aos.errors import AosError

GOOD = "---\nname: deploy-app\ndescription: Deploy the app safely.\n---\n\n# Deploy\n\n1. Run tests.\n"


def test_create_adds_provenance(repo):
    skills.create(repo, "deploy-app", GOOD)
    meta, body = skills.split_frontmatter((repo / "skills/deploy-app/SKILL.md").read_text())
    assert meta["metadata"]["aos"]["created_by"] == "agent"
    assert "1. Run tests." in body


def test_create_validates(repo):
    with pytest.raises(AosError):
        skills.create(repo, "Bad Name", GOOD)
    with pytest.raises(AosError):
        skills.create(repo, "other", GOOD)
    with pytest.raises(AosError):
        skills.create(repo, "deploy-app", "no frontmatter")
    skills.create(repo, "deploy-app", GOOD)
    with pytest.raises(AosError):
        skills.create(repo, "deploy-app", GOOD)


def test_list_and_project_filter(repo, make_skill):
    make_skill("general")
    make_skill("shop-only", projects=["shop"])
    assert [s["name"] for s in skills.list_skills(repo)] == ["general", "shop-only"]
    assert skills.skills_for(repo, "shop") == ["general", "shop-only"]
    assert skills.skills_for(repo, "crm") == ["general"]


def test_patch_unique(repo):
    skills.create(repo, "deploy-app", GOOD)
    skills.patch(repo, "deploy-app", "1. Run tests.", "1. Run tests.\n2. Tag release.")
    assert "Tag release" in skills.view(repo, "deploy-app")["content"]
    with pytest.raises(AosError):
        skills.patch(repo, "deploy-app", "nothing here", "x")
    with pytest.raises(AosError):
        skills.patch(repo, "deploy-app", "name: deploy-app", "name: renamed")
    assert "name: deploy-app" in skills.view(repo, "deploy-app")["content"]


def test_write_file_and_traversal(repo):
    skills.create(repo, "deploy-app", GOOD)
    skills.write_file(repo, "deploy-app", "scripts/run.sh", "echo hi\n")
    assert skills.view(repo, "deploy-app")["files"] == ["scripts/run.sh"]
    with pytest.raises(AosError):
        skills.write_file(repo, "deploy-app", "../../evil.md", "x")
    with pytest.raises(AosError):
        skills.write_file(repo, "deploy-app", "SKILL.md", "x")


def test_delete_archives(repo):
    skills.create(repo, "deploy-app", GOOD)
    skills.delete(repo, "deploy-app")
    assert not (repo / "skills/deploy-app").exists()
    assert (repo / "skills/.archive/deploy-app/SKILL.md").exists()
    assert skills.list_skills(repo) == []
    with pytest.raises(AosError):
        skills.view(repo, "deploy-app")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skills.py -q`
Expected: FAIL with `ImportError: cannot import name 'skills'`

- [ ] **Step 3: Implement**

`aos/skills.py`:
```python
"""Canonical skills in agenticOS/skills/<name>/SKILL.md (agentskills.io layout)."""

from __future__ import annotations

import re
import shutil
from datetime import date
from pathlib import Path

import yaml

from .errors import AosError
from .store import inside, write_atomic

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def skills_dir(repo: str | Path) -> Path:
    return Path(repo) / "skills"


def check_name(name: str) -> None:
    if not NAME_RE.match(name or ""):
        raise AosError(f"invalid skill name {name!r}", "kebab-case: a-z, 0-9, '-', max 64 chars")


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise AosError("SKILL.md must start with YAML frontmatter ('---')")
    end = text.find("\n---", 3)
    if end == -1:
        raise AosError("SKILL.md frontmatter is not terminated with '---'")
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as e:
        raise AosError(f"invalid frontmatter YAML: {e}")
    if not isinstance(meta, dict):
        raise AosError("frontmatter must be a YAML mapping")
    body = text[end + 4:].lstrip("\n")
    return meta, body


def join_frontmatter(meta: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n\n" + body


def validate(name: str, text: str) -> dict:
    meta, _ = split_frontmatter(text)
    if meta.get("name") != name:
        raise AosError(f"frontmatter name {meta.get('name')!r} must equal skill name {name!r}")
    if not str(meta.get("description") or "").strip():
        raise AosError("frontmatter needs a non-empty description")
    return meta


def _skill_md(repo, name: str) -> Path:
    check_name(name)
    return skills_dir(repo) / name / "SKILL.md"


def _existing(repo, name: str) -> Path:
    md = _skill_md(repo, name)
    if not md.exists():
        raise AosError(f"no skill named {name!r}", "call skill_list")
    return md


def list_skills(repo) -> list[dict]:
    out = []
    for md in sorted(skills_dir(repo).glob("*/SKILL.md")):
        if md.parent.name.startswith("."):
            continue
        try:
            meta, _ = split_frontmatter(md.read_text(encoding="utf-8"))
        except AosError:
            continue
        aos_meta = ((meta.get("metadata") or {}).get("aos")) or {}
        out.append({
            "name": md.parent.name,
            "description": str(meta.get("description") or ""),
            "projects": aos_meta.get("projects"),
        })
    return out


def skills_for(repo, slug: str) -> list[str]:
    return [s["name"] for s in list_skills(repo) if not s["projects"] or slug in s["projects"]]


def view(repo, name: str) -> dict:
    md = _existing(repo, name)
    files = sorted(
        p.relative_to(md.parent).as_posix()
        for p in md.parent.rglob("*")
        if p.is_file() and p != md
    )
    return {"name": name, "content": md.read_text(encoding="utf-8"), "files": files}


def create(repo, name: str, content: str, created_by: str = "agent") -> dict:
    md = _skill_md(repo, name)
    if md.exists():
        raise AosError(f"skill {name!r} already exists", "use action=patch to change it")
    meta = validate(name, content)
    _, body = split_frontmatter(content)
    metadata = meta.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise AosError("frontmatter 'metadata' must be a mapping")
    aos_meta = metadata.setdefault("aos", {})
    aos_meta.setdefault("created_by", created_by)
    aos_meta.setdefault("created", date.today().isoformat())
    write_atomic(md, join_frontmatter(meta, body))
    return {"created": name}


def patch(repo, name: str, old: str, new: str) -> dict:
    md = _existing(repo, name)
    text = md.read_text(encoding="utf-8")
    n = text.count(old) if old else 0
    if n != 1:
        raise AosError(f"old text matched {n} times in {name}/SKILL.md", "it must match exactly once")
    out = text.replace(old, new, 1)
    validate(name, out)
    write_atomic(md, out)
    return {"patched": name}


def write_file(repo, name: str, rel: str, content: str) -> dict:
    md = _existing(repo, name)
    p = inside(md.parent, rel)
    if p == md.resolve():
        raise AosError("use action=patch to change SKILL.md")
    write_atomic(p, content)
    return {"written": f"{name}/{p.relative_to(md.parent.resolve()).as_posix()}"}


def delete(repo, name: str) -> dict:
    md = _existing(repo, name)
    dest = skills_dir(repo) / ".archive" / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(md.parent), str(dest))
    return {"archived": name}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skills.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add aos/skills.py tests/test_skills.py
git commit -m "feat: skill CRUD with validation and archive-on-delete"
```

---

### Task 4: Inbox and AOS facade

**Files:**
- Create: `aos/inbox.py`, `aos/core.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `Memory`, `skills.*`, `load_settings`, `aos_home`, `check_slug`, store helpers
- Produces:
  - `inbox.stage(repo, kind, op, args, source="", reason="") -> str`, `inbox.list_proposals(repo) -> list[dict]`, `inbox.load(repo, pid) -> dict`, `inbox.discard(repo, pid) -> None`
  - `AOS(repo, home=None, slug=None)` with `.repo`, `.home`, `.slug`, `.settings`, `.memory_path(scope, slug=None)`, `.memory(scope, slug=None) -> Memory`, `.memory_read(scope) -> dict`, `.memory_write(scope, op, args, source="", reason="", direct=False) -> dict` (staged → `{"staged": id, "message"}`), `.skill_write(action, name, args, source="", reason="", direct=False) -> dict` (applied → includes `"applied": True`), `.apply_proposal(pid) -> dict`, `.reject_proposal(pid)`, `.projects() -> dict`, `.register_project(slug, description="")`, `.project_list() -> list[{"slug","description"}]`, `.project_context(slug) -> {"slug","description","memory"}`

- [ ] **Step 1: Write the failing test**

`tests/test_core.py`:
```python
import pytest

from aos import inbox
from aos.core import AOS
from aos.errors import AosError

GOOD = "---\nname: deploy-app\ndescription: Deploy the app safely.\n---\n\nSteps.\n"


def test_project_memory_direct(repo, home):
    res = AOS(repo, slug="shop").memory_write("project", "add", {"text": "Shop uses Stripe."})
    assert res["entries"] == ["Shop uses Stripe."]
    assert (repo / "memory/projects/shop.md").exists()


def test_org_memory_staged_then_applied(repo, home):
    aos = AOS(repo, slug="shop")
    res = aos.memory_write("org", "add", {"text": "Fiscal year starts in April."},
                           source="project:shop", reason="user said so")
    assert "staged" in res
    assert aos.memory("org").entries() == []
    [p] = inbox.list_proposals(repo)
    assert p["args"]["scope"] == "org" and p["reason"] == "user said so"
    aos.apply_proposal(res["staged"])
    assert aos.memory("org").entries() == ["Fiscal year starts in April."]
    assert inbox.list_proposals(repo) == []


def test_reject_proposal(repo, home):
    aos = AOS(repo)
    pid = aos.memory_write("org", "add", {"text": "x"})["staged"]
    aos.reject_proposal(pid)
    assert inbox.list_proposals(repo) == []
    with pytest.raises(AosError):
        aos.apply_proposal(pid)
    with pytest.raises(AosError):
        aos.apply_proposal("../../SOUL")


def test_scope_errors(repo, home):
    with pytest.raises(AosError):
        AOS(repo).memory_read("project")
    with pytest.raises(AosError):
        AOS(repo).memory_read("team")


def test_user_memory_lives_in_home(repo, home):
    AOS(repo).memory_write("user", "add", {"text": "Prefers Spanish."})
    assert (home / "user.md").read_text().startswith("Prefers Spanish.")


def test_missing_args(repo, home):
    with pytest.raises(AosError):
        AOS(repo).memory_write("user", "replace", {"old": "a"})
    with pytest.raises(AosError):
        AOS(repo).memory_write("user", "explode", {})


def test_skill_inbox_mode(repo, home):
    (repo / "aos.yaml").write_text("skills:\n  mode: inbox\n")
    aos = AOS(repo, slug="shop")
    res = aos.skill_write("create", "deploy-app", {"content": GOOD})
    assert "staged" in res
    assert not (repo / "skills/deploy-app").exists()
    assert aos.apply_proposal(res["staged"])["applied"]
    assert (repo / "skills/deploy-app/SKILL.md").exists()


def test_skill_direct_mode(repo, home):
    res = AOS(repo).skill_write("create", "deploy-app", {"content": GOOD})
    assert res == {"created": "deploy-app", "applied": True}


def test_projects_registry(repo, home):
    aos = AOS(repo, slug="shop")
    aos.register_project("shop", "Online shop API")
    aos.register_project("shop", "ignored")
    aos.memory_write("project", "add", {"text": "Uses Stripe."})
    assert aos.project_list() == [{"slug": "shop", "description": "Online shop API"}]
    ctx = AOS(repo, slug="crm").project_context("shop")
    assert ctx["memory"] == ["Uses Stripe."]
    with pytest.raises(AosError):
        aos.project_context("nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_core.py -q`
Expected: FAIL with `ImportError: cannot import name 'inbox'`

- [ ] **Step 3: Implement**

`aos/inbox.py`:
```python
"""Proposals awaiting human approval: one YAML file each in agenticOS/inbox/."""

from __future__ import annotations

import secrets
import time
from pathlib import Path

import yaml

from .errors import AosError
from .store import inside, read_text, write_atomic


def inbox_dir(repo) -> Path:
    return Path(repo) / "inbox"


def stage(repo, kind: str, op: str, args: dict, source: str = "", reason: str = "") -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    pid = f"{ts}-{kind}-{secrets.token_hex(3)}"
    data = {"id": pid, "kind": kind, "op": op, "args": args,
            "source": source, "reason": reason, "created": ts}
    write_atomic(inbox_dir(repo) / f"{pid}.yaml", yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return pid


def _path(repo, pid: str) -> Path:
    p = inside(inbox_dir(repo), f"{pid}.yaml")
    if not p.is_file():
        raise AosError(f"no proposal {pid!r}", "run `aos inbox list`")
    return p


def load(repo, pid: str) -> dict:
    return yaml.safe_load(read_text(_path(repo, pid)))


def discard(repo, pid: str) -> None:
    _path(repo, pid).unlink()


def list_proposals(repo) -> list[dict]:
    d = inbox_dir(repo)
    return [yaml.safe_load(read_text(p)) for p in sorted(d.glob("*.yaml"))] if d.is_dir() else []
```

`aos/core.py`:
```python
"""AOS: the one facade the CLI and MCP server use. Applies scopes and write modes."""

from __future__ import annotations

from pathlib import Path

import yaml

from . import inbox, skills
from .config import aos_home, check_slug, load_settings
from .errors import AosError
from .memory import Memory
from .store import locked, read_text, write_atomic

SCOPES = ("org", "project", "user")
MEMORY_OPS = {"add": ("text",), "replace": ("old", "new"), "remove": ("old",)}
SKILL_OPS = {"create": ("content",), "patch": ("old", "new"), "write_file": ("path", "content"), "delete": ()}


def _require(op: str, table: dict, args: dict) -> None:
    if op not in table:
        raise AosError(f"unknown action {op!r}", "one of: " + ", ".join(table))
    missing = [k for k in table[op] if args.get(k) is None]
    if missing:
        raise AosError(f"{op} needs: {', '.join(missing)}")


class AOS:
    def __init__(self, repo: str | Path, home: str | Path | None = None, slug: str | None = None):
        self.repo = Path(repo)
        self.home = Path(home) if home else aos_home()
        self.slug = slug
        self.settings = load_settings(self.repo)

    # -- memory --------------------------------------------------------------
    def memory_path(self, scope: str, slug: str | None = None) -> Path:
        if scope == "org":
            return self.repo / "memory" / "org.md"
        if scope == "user":
            return self.home / "user.md"
        if scope == "project":
            s = slug or self.slug
            if not s:
                raise AosError("no project in this session", "run inside a linked project (aos link)")
            check_slug(s)
            return self.repo / "memory" / "projects" / f"{s}.md"
        raise AosError(f"unknown scope {scope!r}", "one of: org, project, user")

    def memory(self, scope: str, slug: str | None = None) -> Memory:
        path = self.memory_path(scope, slug)
        return Memory(path, int(self.settings["memory"][scope]["cap"]))

    def memory_read(self, scope: str) -> dict:
        return self.memory(scope).snapshot()

    def memory_write(self, scope: str, op: str, args: dict, source: str = "",
                     reason: str = "", direct: bool = False) -> dict:
        _require(op, MEMORY_OPS, args)
        mem = self.memory(scope, args.get("slug"))
        if not direct and self.settings["memory"][scope]["mode"] == "inbox":
            payload = {**args, "scope": scope}
            if scope == "project":
                payload.setdefault("slug", self.slug)
            pid = inbox.stage(self.repo, "memory", op, payload, source, reason)
            return {"staged": pid, "message": f"{scope} memory change staged for team review"}
        if op == "add":
            return mem.add(args["text"])
        if op == "replace":
            return mem.replace(args["old"], args["new"])
        return mem.remove(args["old"])

    # -- skills --------------------------------------------------------------
    def skill_write(self, action: str, name: str, args: dict, source: str = "",
                    reason: str = "", direct: bool = False) -> dict:
        _require(action, SKILL_OPS, args)
        skills.check_name(name)
        if not direct and self.settings["skills"]["mode"] == "inbox":
            pid = inbox.stage(self.repo, "skill", action, {**args, "name": name}, source, reason)
            return {"staged": pid, "message": "skill change staged for team review"}
        if action == "create":
            res = skills.create(self.repo, name, args["content"])
        elif action == "patch":
            res = skills.patch(self.repo, name, args["old"], args["new"])
        elif action == "write_file":
            res = skills.write_file(self.repo, name, args["path"], args["content"])
        else:
            res = skills.delete(self.repo, name)
        return {**res, "applied": True}

    # -- inbox ---------------------------------------------------------------
    def apply_proposal(self, pid: str) -> dict:
        p = inbox.load(self.repo, pid)
        args = dict(p.get("args") or {})
        if p.get("kind") == "memory":
            res = self.memory_write(args.pop("scope"), p["op"], args, direct=True)
        elif p.get("kind") == "skill":
            res = self.skill_write(p["op"], args.pop("name"), args, direct=True)
        else:
            raise AosError(f"unknown proposal kind {p.get('kind')!r}")
        inbox.discard(self.repo, pid)
        return {**res, "applied": True}

    def reject_proposal(self, pid: str) -> None:
        inbox.discard(self.repo, pid)

    # -- projects ------------------------------------------------------------
    def _projects_file(self) -> Path:
        return self.repo / "projects.yaml"

    def projects(self) -> dict:
        data = yaml.safe_load(read_text(self._projects_file())) or {}
        return data.get("projects") or {}

    def register_project(self, slug: str, description: str = "") -> None:
        check_slug(slug)
        with locked(self._projects_file()):
            ps = self.projects()
            if slug in ps:
                return
            ps[slug] = {"description": description}
            write_atomic(self._projects_file(),
                         yaml.safe_dump({"projects": ps}, sort_keys=True, allow_unicode=True))

    def project_list(self) -> list[dict]:
        return [{"slug": s, "description": (info or {}).get("description", "")}
                for s, info in sorted(self.projects().items())]

    def project_context(self, slug: str) -> dict:
        ps = self.projects()
        if slug not in ps:
            raise AosError(f"unknown project {slug!r}", "call project_list")
        return {"slug": slug, "description": (ps[slug] or {}).get("description", ""),
                "memory": self.memory("project", slug).entries()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_core.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add aos/inbox.py aos/core.py tests/test_core.py
git commit -m "feat: AOS facade with write modes, inbox and project registry"
```

---

### Task 5: Knowledge search

**Files:**
- Create: `aos/knowledge.py`
- Test: `tests/test_knowledge.py`

**Interfaces:**
- Consumes: `inside`, `split_frontmatter`, `AosError`
- Produces: `KnowledgeIndex(repo, db_path)` with `.refresh() -> bool`, `.search(query, limit=10) -> list[{"path","title","snippet","score"}]`, `.read(rel) -> {"path","content"}`

- [ ] **Step 1: Write the failing test**

`tests/test_knowledge.py`:
```python
import pytest

from aos.errors import AosError
from aos.knowledge import KnowledgeIndex


def idx(repo, home):
    return KnowledgeIndex(repo, home / "aos.db")


def test_search_finds_knowledge_memory_and_skills(repo, home, make_skill):
    (repo / "knowledge/billing.md").write_text("# Billing\n\nInvoices are issued on the 1st via Stripe.\n")
    (repo / "memory/org.md").write_text("Fiscal year starts in April.\n")
    make_skill("refunds", description="Process a customer refund in Stripe.")
    paths = {r["path"] for r in idx(repo, home).search("stripe")}
    assert paths == {"knowledge/billing.md", "skills/refunds/SKILL.md"}
    [r] = idx(repo, home).search("fiscal april")
    assert r["path"] == "memory/org.md"


def test_title_and_snippet(repo, home):
    (repo / "knowledge/billing.md").write_text("# Billing\n\nInvoices via Stripe.\n")
    [r] = idx(repo, home).search("invoices")
    assert r["title"] == "Billing"
    assert "[Invoices]" in r["snippet"]


def test_reindexes_on_change(repo, home):
    k = idx(repo, home)
    assert k.search("kafka") == []
    (repo / "knowledge/events.md").write_text("We use Kafka.\n")
    assert len(k.search("kafka")) == 1
    (repo / "knowledge/events.md").unlink()
    assert k.search("kafka") == []


def test_query_with_operators_and_punctuation(repo, home):
    (repo / "knowledge/a.md").write_text("multi-tenant AND billing\n")
    k = idx(repo, home)
    for q in ["multi-tenant", "AND", '"billing', "NEAR(x", "*", ""]:
        k.search(q)
    assert k.search("multi-tenant")


def test_read_and_traversal(repo, home):
    (repo / "knowledge/a.md").write_text("hello\n")
    k = idx(repo, home)
    assert k.read("knowledge/a.md") == {"path": "knowledge/a.md", "content": "hello\n"}
    for bad in ["../secret.md", "knowledge/missing.md", "SOUL.md/../../x.md", "aos.yaml"]:
        with pytest.raises(AosError):
            k.read(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_knowledge.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aos.knowledge'`

- [ ] **Step 3: Implement**

`aos/knowledge.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_knowledge.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add aos/knowledge.py tests/test_knowledge.py
git commit -m "feat: FTS5 knowledge search over knowledge, memory and skills"
```

---

### Task 6: Session context

**Files:**
- Create: `aos/context.py`
- Test: `tests/test_context.py`

**Interfaces:**
- Consumes: `AOS` (`.repo`, `.slug`, `.settings`, `.memory()`, `.project_list()`), `read_text`
- Produces: `PROTOCOL: str` (also used by the Kiro steering file), `TRUNC: str`, `build_context(aos) -> str`

- [ ] **Step 1: Write the failing test**

`tests/test_context.py`:
```python
from aos.context import build_context
from aos.core import AOS


def test_context_order_and_content(repo, home):
    aos = AOS(repo, slug="shop")
    aos.register_project("shop", "Shop API")
    aos.register_project("crm", "CRM backend")
    aos.memory_write("project", "add", {"text": "Shop uses Stripe."})
    aos.memory_write("user", "add", {"text": "Prefers Spanish."})
    out = build_context(aos)
    order = ["You are the team agent.", "Use UTC.", "agenticOS memory protocol", "Org memory",
             "(empty)", "Shop uses Stripe.", "Prefers Spanish.", "- crm: CRM backend"]
    pos = [out.index(s) for s in order]
    assert pos == sorted(pos)
    assert "- shop" not in out


def test_context_without_project(repo, home):
    out = build_context(AOS(repo))
    assert "Project memory" not in out and "Org memory" in out


def test_context_budget_trims_project_index(repo, home):
    (repo / "aos.yaml").write_text("context:\n  max_chars: 3000\n")
    aos = AOS(repo, slug="shop")
    for i in range(200):
        aos.register_project(f"p{i}", "x" * 40)
    out = build_context(aos)
    assert len(out) <= 3000
    assert "- p0:" in out
    assert "- p99:" not in out


def test_context_hard_truncates(repo, home):
    (repo / "aos.yaml").write_text("context:\n  max_chars: 500\n")
    (repo / "SOUL.md").write_text("S" * 2000)
    out = build_context(AOS(repo, slug="shop"))
    assert len(out) <= 500
    assert out.rstrip().endswith("[aos: context truncated]")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_context.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aos.context'`

- [ ] **Step 3: Implement**

`aos/context.py`:
```python
"""The text injected at session start (Claude Code SessionStart / Kiro SessionStart hook)."""

from __future__ import annotations

from .core import AOS
from .store import read_text

PROTOCOL = """\
You are running with agenticOS, the team's shared, persistent brain across projects. Its tools are on the `aos` MCP server.

Memory (loaded below, small on purpose):
- Save durable facts only: business rules, domain terms, decisions and their reasons, conventions, environment quirks, user preferences. Never task progress, logs, or anything derivable from the code.
- Scopes: `org` holds for every project and is staged for team review; `project` is this repo; `user` is this person.
- Use memory_add, memory_replace and memory_remove. When a scope is full, consolidate entries, then retry.

Skills (procedures, loaded on demand):
- After a non-trivial multi-step task, a dead end you worked around, or a user correction, save or patch a skill with skill_manage. Write lessons, not logs: the steps that work, the pitfalls, the expected result.
- Check skill_list first; patch an existing skill instead of creating a near-duplicate.

Context:
- Before asking the user about business context, call knowledge_search. For another project, use project_list and project_context.
- For code questions use the graphskill tools (repo_map, search_symbols, callers, read_symbol_body) before grep or reading whole files."""

TRUNC = "\n…[aos: context truncated]\n"


def build_context(aos: AOS) -> str:
    parts = ["# agenticOS context"]

    def section(title: str, text: str) -> None:
        parts.append(f"## {title}\n\n{text.strip()}")

    soul = read_text(aos.repo / "SOUL.md")
    rules = read_text(aos.repo / "AGENTS.md")
    if soul.strip():
        section("Identity (SOUL.md)", soul)
    if rules.strip():
        section("Org rules (AGENTS.md)", rules)
    section("agenticOS memory protocol", PROTOCOL)

    labels = {"org": "Org memory", "project": f"Project memory: {aos.slug}", "user": "User memory"}
    for scope in ("org", "project", "user"):
        if scope == "project" and not aos.slug:
            continue
        snap = aos.memory(scope).snapshot()
        section(f"{labels[scope]} [{snap['used']}/{snap['cap']} chars]",
                "\n§\n".join(snap["entries"]) or "(empty)")

    out = "\n\n".join(parts) + "\n"
    limit = int(aos.settings["context"]["max_chars"])

    lines = [f"- {p['slug']}: {p['description']}" if p["description"] else f"- {p['slug']}"
             for p in aos.project_list() if p["slug"] != aos.slug]
    if lines:
        head = "\n## Other projects (call project_context for details)\n\n"
        kept, size = [], len(out) + len(head)
        for line in lines:
            if size + len(line) + 1 > limit:
                break
            kept.append(line)
            size += len(line) + 1
        if kept:
            out += head + "\n".join(kept) + "\n"

    if len(out) > limit:
        out = out[: limit - len(TRUNC)] + TRUNC
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_context.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add aos/context.py tests/test_context.py
git commit -m "feat: session context builder with budget"
```

---

### Task 7: Writer, adapters, link/sync/unlink

**Files:**
- Create: `aos/writer.py`, `aos/adapters/__init__.py`, `aos/adapters/claude.py`, `aos/adapters/kiro.py`, `aos/link.py`
- Test: `tests/test_link.py`

**Interfaces:**
- Consumes: config (`load_user_config`, `save_user_config`, `repo_root`, `slug_for_path`, `default_slug`, `check_slug`, `graphskill_cmd`), `AOS`, `skills_for`, `PROTOCOL`, store
- Produces:
  - `writer.MANIFEST = ".aos/manifest.json"`; `Writer(project, old_manifest)` with `.file(rel, data)`, `.json_key(rel, keys, value)`, `.hook(rel, event, command)`, `.block(rel, lines)`, `.finish(keep_manifest=True)`, lists `.ignore .changed .removed .conflicts`, `Writer.load_manifest(project) -> dict`
  - `LinkContext(project, slug, repo, aos_bin, skills)` with `.mcp_server -> dict`, `.context_command(project_expr) -> str`; `copy_skills(w, ctx, root)`
  - `adapters.claude.render(w, ctx)`, `adapters.claude.JSON_FILES`; same for `adapters.kiro`
  - `link(project, slug=None, targets=("claude","kiro"), graphskill=True, runner=subprocess.run) -> {"slug","changed","removed","conflicts","graphskill"}`; `sync(project) -> report`; `unlink(project) -> {"slug","removed"}`; `render_project(project, slug, repo, targets) -> report`

- [ ] **Step 1: Write the failing test**

`tests/test_link.py`:
```python
import json
import shutil
import subprocess

import pytest
import yaml

from aos.config import load_user_config, save_user_config
from aos.errors import AosError
from aos.link import link, sync, unlink


def load(p):
    return json.loads(p.read_text())


@pytest.fixture
def env(configured, project, monkeypatch, make_skill):
    monkeypatch.setattr("aos.link.shutil.which", lambda n: f"/bin/{n}" if n == "aos" else None)
    monkeypatch.setattr("aos.config.shutil.which", lambda n: None)
    make_skill("deploy-app", files={"scripts/run.sh": "echo hi\n"})
    make_skill("crm-only", projects=["crm"])
    return project


def test_link_writes_claude_and_kiro(env, configured):
    p = env
    rep = link(p, slug="shop")
    assert load(p / ".mcp.json")["mcpServers"]["aos"] == {
        "command": "/bin/aos", "args": ["serve", "--project", str(p.resolve())]}
    assert load(p / ".claude/settings.local.json")["hooks"]["SessionStart"] == [
        {"hooks": [{"type": "command", "command": '"/bin/aos" context --project "$CLAUDE_PROJECT_DIR"'}]}]
    assert (p / ".claude/skills/deploy-app/scripts/run.sh").read_text() == "echo hi\n"
    assert not (p / ".claude/skills/crm-only").exists()
    assert load(p / ".kiro/settings/mcp.json")["mcpServers"]["aos"]["command"] == "/bin/aos"
    kh = load(p / ".kiro/hooks/aos.json")
    assert kh["version"] == "v1" and kh["hooks"][0]["trigger"] == "SessionStart"
    assert (p / ".kiro/steering/aos.md").read_text().startswith("---\ninclusion: always\n---")
    assert (p / ".kiro/skills/deploy-app/SKILL.md").exists()
    gi = (p / ".gitignore").read_text()
    assert ".aos/" in gi and ".claude/skills/deploy-app/" in gi and ".kiro/skills/deploy-app/" in gi
    assert (configured / "memory/projects/shop.md").exists()
    assert "shop" in yaml.safe_load((configured / "projects.yaml").read_text())["projects"]
    assert load_user_config()["projects"]["shop"]["path"] == str(p.resolve())
    assert rep["graphskill"].startswith("graphskill:")


def test_link_is_idempotent(env):
    link(env, slug="shop")
    rep = link(env, slug="shop")
    assert rep["changed"] == [] and rep["removed"] == [] and rep["conflicts"] == []


def test_preserves_user_config(env):
    p = env
    (p / ".mcp.json").write_text(json.dumps({"mcpServers": {"db": {"command": "pg"}}}))
    (p / ".claude").mkdir()
    user_settings = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo mine"}]}]},
                     "permissions": {"allow": ["Bash(ls)"]}}
    (p / ".claude/settings.local.json").write_text(json.dumps(user_settings))
    (p / ".gitignore").write_text("node_modules/\n")
    link(p, slug="shop")
    unlink(p)
    assert load(p / ".mcp.json") == {"mcpServers": {"db": {"command": "pg"}}}
    assert load(p / ".claude/settings.local.json") == user_settings
    assert (p / ".gitignore").read_text() == "node_modules/\n"
    assert not (p / ".claude/skills").exists()
    assert not (p / ".kiro").exists()
    assert not (p / ".aos").exists()
    assert "shop" not in load_user_config()["projects"]


def test_user_skill_with_same_name_is_conflict(env):
    d = env / ".claude/skills/deploy-app"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("mine")
    rep = link(env, slug="shop")
    assert ".claude/skills/deploy-app" in rep["conflicts"]
    assert (d / "SKILL.md").read_text() == "mine"
    assert not (d / "scripts").exists()
    assert ".claude/skills/deploy-app/" not in (env / ".gitignore").read_text()
    unlink(env)
    assert (d / "SKILL.md").read_text() == "mine"


def test_sync_prunes_skill_deleted_upstream(env, configured):
    link(env, slug="shop")
    shutil.rmtree(configured / "skills/deploy-app")
    rep = sync(env)
    assert not (env / ".claude/skills/deploy-app").exists()
    assert ".claude/skills/deploy-app/SKILL.md" in rep["removed"]
    assert ".claude/skills/deploy-app/" not in (env / ".gitignore").read_text()


def test_malformed_json_aborts_cleanly(env):
    (env / ".mcp.json").write_text("{ not json")
    with pytest.raises(AosError) as e:
        link(env, slug="shop")
    assert ".mcp.json" in e.value.message
    assert (env / ".mcp.json").read_text() == "{ not json"
    assert not (env / ".claude").exists()
    assert "shop" not in load_user_config()["projects"]


def test_slug_taken_by_other_path(env, tmp_path):
    link(env, slug="shop")
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(AosError):
        link(other, slug="shop")


def test_targets_claude_only_then_add_kiro_then_drop_it(env):
    link(env, slug="shop", targets=["claude"])
    assert not (env / ".kiro").exists()
    link(env, slug="shop", targets=["claude", "kiro"])
    assert (env / ".kiro/hooks/aos.json").exists()
    link(env, slug="shop", targets=["claude"])
    assert not (env / ".kiro").exists()
    with pytest.raises(AosError):
        link(env, slug="shop", targets=["cursor"])


def test_graphskill_setup_and_kiro_mirror(env, monkeypatch):
    save_user_config({**load_user_config(), "graphskill_cmd": ["/gs/python", "-m", "graphskill"]})
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        (env / ".mcp.json").write_text(json.dumps({"mcpServers": {"graphskill": {"command": "gs", "args": ["serve"]}}}))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    rep = link(env, slug="shop", runner=runner)
    assert calls == [["/gs/python", "-m", "graphskill", "setup", str(env.resolve())]]
    assert rep["graphskill"] == "graphskill: ok"
    assert load(env / ".kiro/settings/mcp.json")["mcpServers"]["graphskill"] == {"command": "gs", "args": ["serve"]}
    assert set(load(env / ".mcp.json")["mcpServers"]) == {"graphskill", "aos"}


def test_graphskill_failure_is_reported_not_fatal(env):
    save_user_config({**load_user_config(), "graphskill_cmd": ["/gs/graphskill"]})

    def runner(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "No module named 'graphskill'")

    rep = link(env, slug="shop", runner=runner)
    assert "failed" in rep["graphskill"] and "No module named" in rep["graphskill"]
    assert (env / ".mcp.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_link.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aos.link'`

- [ ] **Step 3: Implement `aos/writer.py`**

```python
"""Manifest-owned writes into a linked project.

Every file, JSON key, hook and .gitignore block aos writes is recorded in
.aos/manifest.json. On each render, anything owned last time but not produced
this time is removed; anything not owned is never modified.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .errors import AosError
from .store import read_text, write_atomic

MANIFEST = ".aos/manifest.json"
BEGIN = "# >>> agenticOS (managed) >>>"
END = "# <<< agenticOS (managed) <<<"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _empty(v) -> bool:
    if isinstance(v, dict):
        return all(_empty(x) for x in v.values())
    if isinstance(v, list):
        return all(_empty(x) for x in v)
    return False


def _prune_dirs(d: Path, stop: Path) -> None:
    stop = stop.resolve()
    while d.resolve() != stop and d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def _strip_block(text: str) -> str:
    start = text.find(BEGIN)
    end = text.find(END, start) if start != -1 else -1
    if start == -1 or end == -1:
        return text
    before = text[:start].rstrip("\n")
    after = text[end + len(END):].lstrip("\n")
    if before and after:
        return before + "\n\n" + after
    return before + "\n" if before else after


@dataclass
class LinkContext:
    project: Path
    slug: str
    repo: Path
    aos_bin: str
    skills: list[str]

    @property
    def mcp_server(self) -> dict:
        return {"command": self.aos_bin, "args": ["serve", "--project", str(self.project)]}

    def context_command(self, project_expr: str) -> str:
        return f'"{self.aos_bin}" context --project "{project_expr}"'


class Writer:
    def __init__(self, project: Path, old: dict):
        self.project = Path(project)
        self.old = old
        self.files: dict[str, str] = {}
        self.json_keys: list[list[str]] = []
        self.hooks: list[dict] = []
        self.blocks: list[str] = []
        self.created: set[str] = set(old.get("created", []))
        self.ignore: list[str] = []
        self.changed: list[str] = []
        self.removed: list[str] = []
        self.conflicts: list[str] = []

    @staticmethod
    def load_manifest(project: Path) -> dict:
        text = read_text(Path(project) / MANIFEST)
        try:
            return json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            raise AosError(f"corrupt {MANIFEST}", "delete it and re-run `aos link`")

    # -- primitives ----------------------------------------------------------
    def _write(self, rel: str, data: bytes, track: bool = False) -> None:
        p = self.project / rel
        if track and not p.exists():
            self.created.add(rel)
        write_atomic(p, data)
        self.changed.append(rel)

    def _delete(self, rel: str) -> None:
        p = self.project / rel
        if p.is_file():
            p.unlink()
            self.removed.append(rel)
            _prune_dirs(p.parent, self.project)
        self.created.discard(rel)

    def _load_json(self, rel: str) -> dict:
        text = read_text(self.project / rel)
        if not text.strip():
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise AosError(f"cannot parse {rel}: {e}", "fix or remove the file, then re-run")
        if not isinstance(data, dict):
            raise AosError(f"{rel} must contain a JSON object")
        return data

    def _save_json(self, rel: str, data: dict) -> None:
        if rel in self.created and _empty(data):
            self._delete(rel)
        else:
            self._write(rel, (json.dumps(data, indent=2) + "\n").encode(), track=True)

    # -- owned outputs -------------------------------------------------------
    def file(self, rel: str, data: str | bytes) -> None:
        raw = data.encode("utf-8") if isinstance(data, str) else data
        p = self.project / rel
        if p.exists() and rel not in self.old.get("files", {}):
            self.conflicts.append(rel)
            return
        self.files[rel] = _sha(raw)
        if not p.exists() or p.read_bytes() != raw:
            self._write(rel, raw)

    def json_key(self, rel: str, keys: list[str], value) -> None:
        data = self._load_json(rel)
        node = data
        for k in keys[:-1]:
            if not isinstance(node.get(k, {}), dict):
                raise AosError(f"{rel}: '{k}' is not a JSON object")
            node = node.setdefault(k, {})
        entry, last = [rel, *keys], keys[-1]
        if last in node and node[last] != value and entry not in self.old.get("json_keys", []):
            self.conflicts.append(f"{rel}:{'.'.join(keys)}")
            return
        self.json_keys.append(entry)
        if node.get(last) != value:
            node[last] = value
            self._save_json(rel, data)

    def hook(self, rel: str, event: str, command: str) -> None:
        data = self._load_json(rel)
        groups = data.setdefault("hooks", {}).setdefault(event, [])
        self.hooks.append({"file": rel, "event": event, "command": command})
        if not any(h.get("command") == command for g in groups for h in g.get("hooks", [])):
            groups.append({"hooks": [{"type": "command", "command": command}]})
            self._save_json(rel, data)

    def block(self, rel: str, lines: list[str]) -> None:
        self.blocks.append(rel)
        text = read_text(self.project / rel)
        base = _strip_block(text)
        body = BEGIN + "\n" + "\n".join(lines) + "\n" + END + "\n"
        new = base.rstrip("\n") + "\n\n" + body if base.strip() else body
        if new != text:
            self._write(rel, new.encode(), track=True)

    # -- pruning -------------------------------------------------------------
    def _remove_key(self, rel: str, keys: list[str]) -> None:
        if not (self.project / rel).exists():
            return
        data = self._load_json(rel)
        node = data
        for k in keys[:-1]:
            node = node.get(k)
            if not isinstance(node, dict):
                return
        if keys[-1] in node:
            del node[keys[-1]]
            self._save_json(rel, data)

    def _remove_hook(self, h: dict) -> None:
        rel = h["file"]
        if not (self.project / rel).exists():
            return
        data = self._load_json(rel)
        hooks = data.get("hooks") or {}
        groups = hooks.get(h["event"]) or []
        kept_groups = []
        for g in groups:
            kept = [x for x in g.get("hooks", []) if x.get("command") != h["command"]]
            if kept:
                kept_groups.append({**g, "hooks": kept})
        if kept_groups == groups:
            return
        if kept_groups:
            hooks[h["event"]] = kept_groups
        else:
            hooks.pop(h["event"], None)
        if not hooks:
            data.pop("hooks", None)
        self._save_json(rel, data)

    def _remove_block(self, rel: str) -> None:
        p = self.project / rel
        if not p.exists():
            return
        text = p.read_text(encoding="utf-8")
        base = _strip_block(text)
        if base == text:
            return
        if not base.strip() and rel in self.created:
            self._delete(rel)
        else:
            self._write(rel, (base.rstrip("\n") + "\n").encode())

    def finish(self, keep_manifest: bool = True) -> None:
        for rel in sorted(set(self.old.get("files", {})) - set(self.files)):
            self._delete(rel)
        for entry in self.old.get("json_keys", []):
            if entry not in self.json_keys:
                self._remove_key(entry[0], entry[1:])
        for h in self.old.get("hooks", []):
            if h not in self.hooks:
                self._remove_hook(h)
        for rel in self.old.get("blocks", []):
            if rel not in self.blocks:
                self._remove_block(rel)
        mpath = self.project / MANIFEST
        if keep_manifest:
            manifest = {"files": self.files, "json_keys": self.json_keys, "hooks": self.hooks,
                        "blocks": self.blocks, "created": sorted(self.created)}
            write_atomic(mpath, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        else:
            mpath.unlink(missing_ok=True)
            _prune_dirs(mpath.parent, self.project)


def copy_skills(w: Writer, ctx: LinkContext, root: str) -> None:
    """Copy each synced skill dir to <root>/<name>/, skipping dirs the user owns."""
    owned_files = w.old.get("files", {})
    for name in ctx.skills:
        dest = f"{root}/{name}"
        owned = any(k.startswith(dest + "/") for k in owned_files)
        if (ctx.project / dest).exists() and not owned:
            w.conflicts.append(dest)
            continue
        src = ctx.repo / "skills" / name
        for f in sorted(src.rglob("*")):
            if f.is_file():
                w.file(f"{dest}/{f.relative_to(src).as_posix()}", f.read_bytes())
        w.ignore.append(dest + "/")
```

- [ ] **Step 4: Implement adapters**

`aos/adapters/__init__.py`:
```python
"""Per-tool renderers. Each exposes render(writer, ctx) and JSON_FILES."""
```

`aos/adapters/claude.py`:
```python
"""Claude Code: .mcp.json server, SessionStart hook in settings.local.json, skills."""

from ..writer import LinkContext, Writer, copy_skills

JSON_FILES = [".mcp.json", ".claude/settings.local.json"]


def render(w: Writer, ctx: LinkContext) -> None:
    w.json_key(".mcp.json", ["mcpServers", "aos"], ctx.mcp_server)
    w.hook(".claude/settings.local.json", "SessionStart", ctx.context_command("$CLAUDE_PROJECT_DIR"))
    w.ignore.append(".claude/settings.local.json")
    copy_skills(w, ctx, ".claude/skills")
```

`aos/adapters/kiro.py`:
```python
"""Kiro: workspace MCP config, SessionStart hook, always-on steering, skills."""

import json

from ..context import PROTOCOL
from ..store import read_text
from ..writer import LinkContext, Writer, copy_skills

MCP = ".kiro/settings/mcp.json"
JSON_FILES = [MCP, ".mcp.json"]  # .mcp.json is read to mirror graphskill's server


def _steering(slug: str) -> str:
    return (
        "---\ninclusion: always\n---\n\n# agenticOS\n\n"
        f"This workspace is linked to agenticOS as project `{slug}`. Identity, org rules and "
        "memories are injected at session start by the aos hook; if they are missing, call "
        "memory_read for the org, project and user scopes.\n\n" + PROTOCOL + "\n"
    )


def _graphskill_server(ctx: LinkContext):
    try:
        data = json.loads(read_text(ctx.project / ".mcp.json") or "{}")
    except json.JSONDecodeError:
        return None
    return (data.get("mcpServers") or {}).get("graphskill")


def render(w: Writer, ctx: LinkContext) -> None:
    w.json_key(MCP, ["mcpServers", "aos"], ctx.mcp_server)
    gs = _graphskill_server(ctx)
    if gs:
        w.json_key(MCP, ["mcpServers", "graphskill"], gs)
    hooks = {"version": "v1", "hooks": [{
        "name": "agenticOS context",
        "trigger": "SessionStart",
        "action": {"type": "command", "command": ctx.context_command(str(ctx.project))},
        "timeout": 30,
    }]}
    w.file(".kiro/hooks/aos.json", json.dumps(hooks, indent=2) + "\n")
    w.file(".kiro/steering/aos.md", _steering(ctx.slug))
    w.ignore += [".kiro/hooks/aos.json", ".kiro/steering/aos.md"]
    copy_skills(w, ctx, ".kiro/skills")
```

- [ ] **Step 5: Implement `aos/link.py`**

```python
"""aos link / sync / unlink: make a project agenticOS-aware in Claude Code and Kiro."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .adapters import claude, kiro
from .config import (
    check_slug, default_slug, graphskill_cmd, load_user_config, repo_root,
    save_user_config, slug_for_path,
)
from .core import AOS
from .errors import AosError
from .skills import skills_for
from .store import read_text, write_atomic
from .writer import LinkContext, Writer

TARGETS = {"claude": claude, "kiro": kiro}


def _check_targets(targets) -> list[str]:
    targets = list(targets)
    bad = sorted(set(targets) - set(TARGETS))
    if bad or not targets:
        raise AosError(f"unknown targets: {', '.join(bad) or '(none)'}", "use claude, kiro or claude,kiro")
    return targets


def _preflight(project: Path, targets: list[str]) -> None:
    for rel in sorted({f for t in targets for f in TARGETS[t].JSON_FILES}):
        text = read_text(project / rel)
        if text.strip():
            try:
                json.loads(text)
            except json.JSONDecodeError as e:
                raise AosError(f"cannot parse {rel}: {e}", "fix or remove the file, then re-run")


def setup_graphskill(project: Path, runner=subprocess.run) -> str:
    cmd = graphskill_cmd()
    if not cmd:
        return "graphskill: not found, skipped (install it or set graphskill_cmd in ~/.agenticos/config.yaml)"
    try:
        r = runner([*cmd, "setup", str(project)], capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as e:
        return f"graphskill: setup failed: {e}"
    if r.returncode != 0:
        return f"graphskill: setup failed: {(r.stderr or r.stdout or '').strip()[-300:]}"
    return "graphskill: ok"


def render_project(project: Path, slug: str, repo: Path, targets: list[str]) -> dict:
    w = Writer(project, Writer.load_manifest(project))
    ctx = LinkContext(project, slug, repo, shutil.which("aos") or "aos", skills_for(repo, slug))
    for t in targets:
        TARGETS[t].render(w, ctx)
    w.block(".gitignore", [".aos/", *w.ignore])
    w.finish()
    return {"slug": slug, "changed": w.changed, "removed": w.removed, "conflicts": w.conflicts}


def link(project, slug: str | None = None, targets=("claude", "kiro"),
         graphskill: bool = True, runner=subprocess.run) -> dict:
    project = Path(project).expanduser().resolve()
    if not project.is_dir():
        raise AosError(f"not a directory: {project}")
    targets = _check_targets(targets)
    cfg = load_user_config()
    repo = repo_root(cfg)
    if project == repo:
        raise AosError("cannot link the agenticOS repo to itself")
    slug = slug or slug_for_path(project, cfg) or default_slug(project)
    check_slug(slug)
    other = cfg["projects"].get(slug)
    if other and Path(other["path"]).resolve() != project:
        raise AosError(f"slug {slug!r} is already linked to {other['path']}", "pass --name <other-slug>")
    _preflight(project, targets)

    gs = setup_graphskill(project, runner) if graphskill else "graphskill: disabled"
    cfg["projects"][slug] = {"path": str(project), "targets": targets}
    save_user_config(cfg)
    aos = AOS(repo, slug=slug)
    aos.register_project(slug)
    if not aos.memory_path("project").exists():
        write_atomic(aos.memory_path("project"), "")
    report = render_project(project, slug, repo, targets)
    report["graphskill"] = gs
    return report


def _linked(project) -> tuple[dict, str, dict]:
    cfg = load_user_config()
    slug = slug_for_path(project, cfg)
    if not slug:
        raise AosError(f"{Path(project).resolve()} is not linked", "run `aos link` first")
    return cfg, slug, cfg["projects"][slug]


def sync(project) -> dict:
    cfg, slug, info = _linked(project)
    return render_project(Path(info["path"]), slug, repo_root(cfg), info.get("targets") or list(TARGETS))


def unlink(project) -> dict:
    cfg, slug, info = _linked(project)
    root = Path(info["path"])
    w = Writer(root, Writer.load_manifest(root))
    w.finish(keep_manifest=False)
    del cfg["projects"][slug]
    save_user_config(cfg)
    return {"slug": slug, "removed": w.removed}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_link.py -q`
Expected: all PASS

- [ ] **Step 7: Run the whole suite and commit**

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS

```bash
git add aos/writer.py aos/adapters aos/link.py tests/test_link.py
git commit -m "feat: link/sync/unlink with manifest-owned Claude Code and Kiro adapters"
```

---

### Task 8: MCP server

**Files:**
- Create: `aos/mcp_server.py`
- Test: `tests/test_mcp.py`

**Interfaces:**
- Consumes: `AOS`, `KnowledgeIndex`, `skills.list_skills`, `skills.view`, `config`, `link.sync`
- Produces: `Tools(aos, on_skills_changed=None)` with methods `memory_read, memory_add, memory_replace, memory_remove, knowledge_search, knowledge_read, skill_list, skill_view, skill_manage, project_list, project_context` (all return dicts, never raise); `build_server(tools) -> FastMCP`; `run_server(project) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_mcp.py`:
```python
import asyncio

from aos.core import AOS
from aos.mcp_server import Tools, build_server

GOOD = "---\nname: deploy-app\ndescription: Deploy the app safely.\n---\n\nSteps.\n"


def tools(repo, slug="shop"):
    return Tools(AOS(repo, slug=slug))


def test_memory_tools_roundtrip(repo, home):
    t = tools(repo)
    assert t.memory_add("project", "Uses Stripe.")["entries"] == ["Uses Stripe."]
    assert t.memory_read("project")["used"] > 0
    assert t.memory_replace("project", "Stripe", "Uses Stripe for payments.")["entries"] == ["Uses Stripe for payments."]
    assert t.memory_remove("project", "payments")["entries"] == []
    assert "staged" in t.memory_add("org", "FY starts April.", reason="told by user")


def test_errors_are_returned_not_raised(repo, home):
    t = tools(repo)
    r = t.memory_replace("project", "nope", "x")
    assert "error" in r and "hint" in r
    assert "error" in t.memory_read("team")
    assert "error" in t.knowledge_read("../x.md")
    assert "error" in t.skill_view("missing")
    assert "error" in t.project_context("missing")


def test_skill_manage_triggers_sync(repo, home):
    calls = []
    t = Tools(AOS(repo, slug="shop"), on_skills_changed=lambda: calls.append(1))
    assert t.skill_manage("create", "deploy-app", content=GOOD)["applied"]
    assert calls == [1]
    assert [s["name"] for s in t.skill_list()["skills"]] == ["deploy-app"]
    assert "error" in t.skill_manage("explode", "deploy-app")


def test_sync_failure_does_not_fail_tool(repo, home):
    def boom():
        raise RuntimeError("disk full")

    t = Tools(AOS(repo, slug="shop"), on_skills_changed=boom)
    r = t.skill_manage("create", "deploy-app", content=GOOD)
    assert r["applied"] and "disk full" in r["sync_warning"]


def test_knowledge_and_projects(repo, home):
    (repo / "knowledge/a.md").write_text("# Pricing\nPlans are monthly.\n")
    t = tools(repo)
    assert t.knowledge_search("monthly")["results"][0]["path"] == "knowledge/a.md"
    assert t.knowledge_read("knowledge/a.md")["content"].startswith("# Pricing")
    AOS(repo).register_project("shop", "Shop API")
    assert t.project_list()["projects"] == [{"slug": "shop", "description": "Shop API"}]


def test_server_registers_all_tools(repo, home):
    mcp = build_server(tools(repo))
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {"memory_read", "memory_add", "memory_replace", "memory_remove",
                     "knowledge_search", "knowledge_read", "skill_list", "skill_view",
                     "skill_manage", "project_list", "project_context"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mcp.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aos.mcp_server'`

- [ ] **Step 3: Implement**

`aos/mcp_server.py`:
```python
"""The agent-facing surface: a stdio MCP server named `aos`.

`Tools` holds the behaviour (testable without MCP); `build_server` only
registers thin wrappers. Tool docstrings are what the model reads, so they
say when to use each tool.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

from . import skills
from .config import load_user_config, repo_root, slug_for_path
from .core import AOS
from .errors import AosError
from .knowledge import KnowledgeIndex


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AosError as e:
            return e.to_dict()
        except Exception as e:  # never raise into the MCP transport
            return {"error": f"internal error: {type(e).__name__}: {e}"}
    return wrapper


class Tools:
    def __init__(self, aos: AOS, on_skills_changed=None):
        self.aos = aos
        self.index = KnowledgeIndex(aos.repo, aos.home / "aos.db")
        self.on_skills_changed = on_skills_changed

    def _source(self) -> str:
        return f"project:{self.aos.slug}" if self.aos.slug else "unlinked"

    @_safe
    def memory_read(self, scope: str) -> dict:
        return self.aos.memory_read(scope)

    @_safe
    def memory_add(self, scope: str, text: str, reason: str = "") -> dict:
        return self.aos.memory_write(scope, "add", {"text": text}, self._source(), reason)

    @_safe
    def memory_replace(self, scope: str, old: str, new: str, reason: str = "") -> dict:
        return self.aos.memory_write(scope, "replace", {"old": old, "new": new}, self._source(), reason)

    @_safe
    def memory_remove(self, scope: str, old: str, reason: str = "") -> dict:
        return self.aos.memory_write(scope, "remove", {"old": old}, self._source(), reason)

    @_safe
    def knowledge_search(self, query: str, limit: int = 10) -> dict:
        return {"results": self.index.search(query, limit)}

    @_safe
    def knowledge_read(self, path: str) -> dict:
        return self.index.read(path)

    @_safe
    def skill_list(self) -> dict:
        return {"skills": skills.list_skills(self.aos.repo)}

    @_safe
    def skill_view(self, name: str) -> dict:
        return skills.view(self.aos.repo, name)

    @_safe
    def skill_manage(self, action: str, name: str, content: str | None = None, old: str | None = None,
                     new: str | None = None, path: str | None = None, reason: str = "") -> dict:
        args = {k: v for k, v in {"content": content, "old": old, "new": new, "path": path}.items()
                if v is not None}
        res = self.aos.skill_write(action, name, args, self._source(), reason)
        if res.get("applied") and self.on_skills_changed:
            try:
                self.on_skills_changed()
            except Exception as e:
                res["sync_warning"] = f"skill saved but project sync failed: {e}"
        return res

    @_safe
    def project_list(self) -> dict:
        return {"projects": self.aos.project_list()}

    @_safe
    def project_context(self, slug: str) -> dict:
        return self.aos.project_context(slug)


def build_server(tools: Tools):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("aos")

    @mcp.tool()
    def memory_read(scope: str) -> dict:
        """Read memory entries and usage. scope: org (all projects) | project (this repo) | user (this person)."""
        return tools.memory_read(scope)

    @mcp.tool()
    def memory_add(scope: str, text: str, reason: str = "") -> dict:
        """Save one durable fact (business rule, decision, convention, preference). Not progress or logs. org changes are staged for team review."""
        return tools.memory_add(scope, text, reason)

    @mcp.tool()
    def memory_replace(scope: str, old: str, new: str, reason: str = "") -> dict:
        """Replace the single entry containing substring `old` with `new`. Use to correct or consolidate."""
        return tools.memory_replace(scope, old, new, reason)

    @mcp.tool()
    def memory_remove(scope: str, old: str, reason: str = "") -> dict:
        """Remove the single entry containing substring `old`."""
        return tools.memory_remove(scope, old, reason)

    @mcp.tool()
    def knowledge_search(query: str, limit: int = 10) -> dict:
        """Search shared business knowledge, memories and skills across all projects. Use before asking the user for business context."""
        return tools.knowledge_search(query, limit)

    @mcp.tool()
    def knowledge_read(path: str) -> dict:
        """Read a full markdown document returned by knowledge_search."""
        return tools.knowledge_read(path)

    @mcp.tool()
    def skill_list() -> dict:
        """List shared skills (name + description)."""
        return tools.skill_list()

    @mcp.tool()
    def skill_view(name: str) -> dict:
        """Show a skill's SKILL.md and its support files."""
        return tools.skill_view(name)

    @mcp.tool()
    def skill_manage(action: str, name: str, content: str | None = None, old: str | None = None,
                     new: str | None = None, path: str | None = None, reason: str = "") -> dict:
        """Save procedural knowledge after a non-trivial task, a dead end, or a user correction. action: create (content = full SKILL.md with name/description frontmatter) | patch (old -> new, exact unique match) | write_file (path, content) | delete (archives)."""
        return tools.skill_manage(action, name, content, old, new, path, reason)

    @mcp.tool()
    def project_list() -> dict:
        """List all projects linked to agenticOS."""
        return tools.project_list()

    @mcp.tool()
    def project_context(slug: str) -> dict:
        """Read another project's description and memory."""
        return tools.project_context(slug)

    return mcp


def run_server(project: str | Path) -> None:
    from .link import sync

    cfg = load_user_config()
    repo = repo_root(cfg)
    project = Path(project).expanduser().resolve()
    slug = slug_for_path(project, cfg)

    def resync() -> None:
        if slug:
            sync(project)

    tools = Tools(AOS(repo, slug=slug), on_skills_changed=resync)
    cwd = os.getcwd()
    os.chdir(Path.home())  # FastMCP reads .env from cwd; keep project env files out of it
    try:
        mcp = build_server(tools)
    finally:
        os.chdir(cwd)
    mcp.run()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mcp.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add aos/mcp_server.py tests/test_mcp.py
git commit -m "feat: aos MCP server with memory, knowledge, skill and project tools"
```

---

### Task 9: CLI

**Files:**
- Create: `aos/cli.py`, `aos/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above
- Produces: `main(argv=None) -> int`; subcommands `init, link, unlink, sync, context, serve, inbox {list,show,apply,reject}, status, doctor`

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
from aos.cli import main
from aos.config import load_user_config
from aos.core import AOS


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_init_link_context(repo, home, project, capsys, monkeypatch):
    monkeypatch.setattr("aos.link.shutil.which", lambda n: "/bin/aos" if n == "aos" else None)
    monkeypatch.setattr("aos.config.shutil.which", lambda n: None)
    assert run(["init", str(repo), "--graphskill", "/gs/python -m graphskill"], capsys)[0] == 0
    assert load_user_config()["graphskill_cmd"] == ["/gs/python", "-m", "graphskill"]
    code, out, _ = run(["link", str(project), "--name", "shop", "--no-graphskill"], capsys)
    assert code == 0 and "shop" in out
    code, out, _ = run(["context", "--project", str(project)], capsys)
    assert code == 0 and "You are the team agent." in out


def test_context_never_fails(home, tmp_path, capsys):
    code, out, _ = run(["context", "--project", str(tmp_path)], capsys)
    assert code == 0
    assert out.startswith("[aos]") and len(out.strip().splitlines()) == 1


def test_context_unlinked(configured, project, capsys):
    code, out, _ = run(["context", "--project", str(project)], capsys)
    assert code == 0 and "not linked" in out


def test_errors_exit_1_with_hint(home, capsys):
    code, _, err = run(["link", "."], capsys)
    assert code == 1 and "aos init" in err


def test_init_rejects_non_repo(home, tmp_path, capsys):
    assert run(["init", str(tmp_path)], capsys)[0] == 1


def test_inbox_flow(configured, capsys):
    pid = AOS(configured).memory_write("org", "add", {"text": "FY starts April."})["staged"]
    code, out, _ = run(["inbox", "list"], capsys)
    assert pid in out
    assert run(["inbox", "show", pid], capsys)[0] == 0
    assert run(["inbox", "apply", pid], capsys)[0] == 0
    assert AOS(configured).memory("org").entries() == ["FY starts April."]
    assert run(["inbox", "reject", pid], capsys)[0] == 1


def test_bad_targets(configured, project, capsys):
    code, _, err = run(["link", str(project), "--targets", "cursor"], capsys)
    assert code == 1 and "cursor" in err


def test_doctor(configured, capsys):
    code, out, _ = run(["doctor"], capsys)
    assert code == 0 and "repo" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aos.cli'`

- [ ] **Step 3: Implement**

`aos/cli.py`:
```python
"""`aos` command line: for humans and hooks. Agents use the MCP server instead."""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from . import inbox
from .config import aos_home, load_user_config, repo_root, save_user_config, slug_for_path
from .core import AOS
from .errors import AosError


def _print_report(rep: dict) -> None:
    print(f"project: {rep['slug']}")
    for label, key in (("wrote", "changed"), ("removed", "removed"), ("conflict, left untouched", "conflicts")):
        for item in rep.get(key, []):
            print(f"  {label}: {item}")
    if rep.get("graphskill"):
        print(f"  {rep['graphskill']}")


def cmd_init(a) -> int:
    repo = Path(a.repo).expanduser().resolve()
    if not (repo / "SOUL.md").exists():
        raise AosError(f"not an agenticOS repo: {repo}", "expected SOUL.md at its root")
    cfg = load_user_config()
    cfg["repo"] = str(repo)
    if a.graphskill:
        cfg["graphskill_cmd"] = shlex.split(a.graphskill)
    save_user_config(cfg)
    print(f"agenticOS repo: {repo}\nuser state:     {aos_home()}")
    return 0


def cmd_link(a) -> int:
    from .link import link
    targets = [t.strip() for t in a.targets.split(",") if t.strip()]
    _print_report(link(a.project, slug=a.name, targets=targets, graphskill=not a.no_graphskill))
    print("restart Claude Code / Kiro in that project to load agenticOS")
    return 0


def cmd_unlink(a) -> int:
    from .link import unlink
    rep = unlink(a.project)
    print(f"unlinked {rep['slug']} ({len(rep['removed'])} files removed)")
    return 0


def cmd_sync(a) -> int:
    from .link import sync
    _print_report(sync(a.project))
    return 0


def cmd_context(a) -> int:
    """SessionStart hook. Must never fail or print more than one line on error."""
    try:
        from .context import build_context
        from .link import sync
        cfg = load_user_config()
        repo = repo_root(cfg)
        slug = slug_for_path(a.project, cfg)
        if not slug:
            print(f"[aos] {Path(a.project).resolve()} is not linked to agenticOS (run `aos link`)")
            return 0
        sync_note = ""
        try:
            sync(a.project)
        except Exception as e:
            sync_note = f"[aos] skill sync skipped: {e}\n"
        sys.stdout.write(sync_note + build_context(AOS(repo, slug=slug)))
    except Exception as e:
        print(f"[aos] context unavailable: {e}")
    return 0


def cmd_serve(a) -> int:
    from .mcp_server import run_server
    run_server(a.project)
    return 0


def cmd_inbox(a) -> int:
    repo = repo_root()
    if a.action == "list":
        items = inbox.list_proposals(repo)
        if not items:
            print("inbox empty")
        for p in items:
            print(f"{p['id']}  {p['kind']}.{p['op']}  {p.get('source') or '-'}  {p.get('reason') or ''}")
    elif a.action == "show":
        print(yaml.safe_dump(inbox.load(repo, a.id), sort_keys=False, allow_unicode=True), end="")
    elif a.action == "apply":
        AOS(repo).apply_proposal(a.id)
        print(f"applied {a.id}")
    else:
        AOS(repo).reject_proposal(a.id)
        print(f"rejected {a.id}")
    return 0


def cmd_status(a) -> int:
    repo = repo_root()
    r = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--",
         "memory", "skills", "knowledge", "inbox", "projects.yaml"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise AosError("git status failed", r.stderr.strip())
    print(r.stdout.rstrip() or "no uncommitted agenticOS changes")
    print(f"inbox: {len(inbox.list_proposals(repo))} proposal(s)")
    return 0


def cmd_doctor(a) -> int:
    def line(ok: bool, text: str) -> None:
        print(("ok    " if ok else "warn  ") + text)

    line(shutil.which("aos") is not None, "aos on PATH" if shutil.which("aos") else
         "aos not on PATH (hooks/MCP use the absolute path recorded at link time)")
    try:
        repo = repo_root()
    except AosError as e:
        print(f"FAIL  {e.message} ({e.hint})")
        return 1
    line(True, f"repo {repo}")
    cfg = load_user_config()
    gs = cfg.get("graphskill_cmd") or ([shutil.which("graphskill")] if shutil.which("graphskill") else None)
    line(bool(gs), f"graphskill: {' '.join(gs)}" if gs else "graphskill not found (aos init --graphskill '<cmd>')")
    for slug, info in sorted(cfg["projects"].items()):
        p = Path(info["path"])
        line((p / ".aos/manifest.json").exists(), f"project {slug}: {p}")
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aos", description="agenticOS: shared brain for Claude Code and Kiro")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="point this machine at an agenticOS repo")
    s.add_argument("repo", nargs="?", default=".")
    s.add_argument("--graphskill", help="command that runs graphskill, e.g. '/path/.venv/bin/python -m graphskill'")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("link", help="make a project agenticOS-aware")
    s.add_argument("project", nargs="?", default=".")
    s.add_argument("--name", help="project slug (default: directory name)")
    s.add_argument("--targets", default="claude,kiro")
    s.add_argument("--no-graphskill", action="store_true")
    s.set_defaults(fn=cmd_link)

    for name, fn, text in (("unlink", cmd_unlink, "remove everything aos added to a project"),
                           ("sync", cmd_sync, "re-render skills and config into a project")):
        s = sub.add_parser(name, help=text)
        s.add_argument("project", nargs="?", default=".")
        s.set_defaults(fn=fn)

    for name, fn, text in (("context", cmd_context, "print session context (SessionStart hook)"),
                           ("serve", cmd_serve, "run the MCP server over stdio")):
        s = sub.add_parser(name, help=text)
        s.add_argument("--project", default=".")
        s.set_defaults(fn=fn)

    s = sub.add_parser("inbox", help="review staged memory/skill proposals")
    isub = s.add_subparsers(dest="action", required=True)
    isub.add_parser("list")
    for action in ("show", "apply", "reject"):
        isub.add_parser(action).add_argument("id")
    s.set_defaults(fn=cmd_inbox)

    sub.add_parser("status", help="uncommitted agenticOS changes").set_defaults(fn=cmd_status)
    sub.add_parser("doctor", help="check installation").set_defaults(fn=cmd_doctor)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.fn(args)
    except AosError as e:
        print(f"aos: {e.message}", file=sys.stderr)
        if e.hint:
            print(f"  hint: {e.hint}", file=sys.stderr)
        return 1
```

`aos/__main__.py`:
```python
from .cli import main

raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add aos/cli.py aos/__main__.py tests/test_cli.py
git commit -m "feat: aos CLI"
```

---

### Task 10: Seed content and README

**Files:**
- Create: `SOUL.md`, `AGENTS.md`, `aos.yaml`, `projects.yaml`, `memory/org.md`, `memory/projects/.gitkeep`, `knowledge/README.md`, `inbox/.gitkeep`, `skills/aos-memory/SKILL.md`, `skills/aos-onboard-project/SKILL.md`, `README.md`
- Test: `tests/test_seed.py`

**Interfaces:**
- Consumes: `skills.list_skills`, `skills.validate`, `load_settings`, `AOS`, `build_context`

- [ ] **Step 1: Write the failing test**

`tests/test_seed.py`:
```python
from pathlib import Path

from aos import skills
from aos.config import load_settings
from aos.context import build_context
from aos.core import AOS

ROOT = Path(__file__).resolve().parents[1]


def test_seed_skills_valid():
    names = [s["name"] for s in skills.list_skills(ROOT)]
    assert {"aos-memory", "aos-onboard-project"} <= set(names)
    for n in names:
        skills.validate(n, (ROOT / "skills" / n / "SKILL.md").read_text())


def test_seed_settings():
    assert load_settings(ROOT)["memory"]["org"]["mode"] == "inbox"


def test_seed_context_fits_budget(home):
    out = build_context(AOS(ROOT, slug="demo"))
    assert len(out) <= 16000 and "agenticOS memory protocol" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_seed.py -q`
Expected: FAIL (`assert {...} <= set()`)

- [ ] **Step 3: Write seed content**

`SOUL.md`:
```markdown
You are the team's engineering agent, working inside one of our projects with access to agenticOS, our shared memory across projects.

Be direct. Match the length of a reply to the weight of the ask. Finished work gets a short report of what changed, what was verified, and what is left. No filler, no restating the request, no narrating tool calls. Say plainly when you are unsure. Agree because something is right, not because the user said it.

Build on what the team already knows: read the memories below, search knowledge before asking, and save what you learn so the next session does not start from zero.
```

`AGENTS.md`:
```markdown
# Org rules

Rules here apply to every project linked to agenticOS. Keep this file short and imperative; long-form material belongs in `knowledge/`.

## Engineering
- Work test-first for behaviour changes; run the tests before claiming done.
- Never commit secrets. Never push to the default branch without being asked.

## Business context
- Our business vocabulary and rules live in org memory and `knowledge/`. Prefer them over assumptions.

## Communication
- Report outcomes faithfully: say what failed, what was skipped, and why.
```

`aos.yaml`:
```yaml
# agenticOS settings. Values here override aos/config.py DEFAULTS.
memory:
  org:     {cap: 4000, mode: inbox}   # shared by every project: team reviews changes
  project: {cap: 2200, mode: direct}
  user:    {cap: 1375, mode: direct}  # stored in ~/.agenticos/user.md, never in this repo
skills:
  mode: direct
context:
  max_chars: 16000
```

`projects.yaml`:
```yaml
projects: {}
```

`memory/org.md`: empty file. `memory/projects/.gitkeep`, `inbox/.gitkeep`: empty files.

`knowledge/README.md`:
```markdown
# Knowledge

Long-form business knowledge: domain model, glossary, ADRs, pricing rules, pages distilled from Notion/Confluence. One topic per file, `# Title` on the first line. Agents search it with `knowledge_search`.
```

`skills/aos-memory/SKILL.md`:
```markdown
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
```

`skills/aos-onboard-project/SKILL.md`:
```markdown
---
name: aos-onboard-project
description: Build the agenticOS project memory for a newly linked repo by combining the code graph with a short interview. Use right after `aos link`, or when project memory is empty.
---

# Onboard a project into agenticOS

1. `memory_read("project")`. If it already has entries, ask whether to refresh or stop.
2. Orient on the code with graphskill: `repo_map()`, then `module_overview()` on the 2-3 biggest areas. Do not read whole files.
3. `knowledge_search` for the project name and its main domain terms; `project_list` to see related projects.
4. Ask the user at most five questions, one at a time, about what the code cannot tell you:
   - What business problem does this service solve, and for whom?
   - Which other projects or external systems does it depend on?
   - Which rules or invariants must never be broken?
   - Where does it run (environments, deploy path), and who owns it?
   - What has bitten people before?
5. Save 4-8 entries with `memory_add("project", ...)`: purpose, dependencies, invariants, environments, gotchas. Keep the total under the cap.
6. Facts that are true beyond this project go to `memory_add("org", ...)`, which is staged for review.
7. Suggest a one-line description for `projects.yaml` and show the user what was saved.
```

`README.md`:
````markdown
# agenticOS

A shared, self-improving brain for **Claude Code** and **Kiro** across all our projects, modelled on the learning loop of [hermes-agent](https://github.com/NousResearch/hermes-agent). Code knowledge comes from **graphskill**.

- **Memory**: small, capped, always-in-context facts: `org` (all projects, team-reviewed), `project`, `user`.
- **Skills**: shared procedures (`skills/<name>/SKILL.md`) that agents can create and patch.
- **Knowledge**: long-form docs in `knowledge/`, searchable by agents.
- **Context injection**: every session starts with SOUL + org rules + memories.

## Install (once per developer)

```bash
git clone <this repo> ~/agenticOS && cd ~/agenticOS
python3.13 -m venv .venv && .venv/bin/pip install -e .
ln -s "$PWD/.venv/bin/aos" ~/.local/bin/aos      # or put .venv/bin on PATH
aos init . --graphskill "graphskill"             # or '/path/to/venv/bin/python -m graphskill'
aos doctor
```

## Use

```bash
aos link ~/code/shop-api --name shop     # Claude Code + Kiro + graphskill
aos link ~/code/crm --targets kiro       # one tool only
aos sync ~/code/shop-api                 # also happens automatically at session start
aos inbox list | show <id> | apply <id> | reject <id>
aos status                               # uncommitted memory/skill changes to push
aos unlink ~/code/shop-api               # removes only what aos added
```

Then open the project in Claude Code or Kiro. Agents get the `aos` MCP tools: `memory_*`, `knowledge_search/read`, `skill_list/view/manage`, `project_list/context`.

**Sharing:** memory, skills and knowledge are files in this repo. Commit and push them; teammates `git pull` and the next session picks them up. Org-memory changes made by agents wait in `inbox/` for review.

## What `aos link` writes

| Claude Code | Kiro |
|---|---|
| `.mcp.json` → `aos` server | `.kiro/settings/mcp.json` → `aos` (+ graphskill mirror) |
| `.claude/settings.local.json` → SessionStart hook | `.kiro/hooks/aos.json` → SessionStart hook |
| `.claude/skills/<name>/` | `.kiro/skills/<name>/`, `.kiro/steering/aos.md` |

Ownership is tracked in `<project>/.aos/manifest.json`; aos never edits files or keys it did not create.

## Layout

```
SOUL.md  AGENTS.md  aos.yaml  projects.yaml
memory/org.md  memory/projects/<slug>.md
knowledge/  skills/  inbox/
aos/  (python package)   tests/
```

## Roadmap

1. ✅ Core (this release)
2. Learning loop: Stop-hook background review → inbox proposals
3. Session search + skill curator
4. Task board + Notion/Confluence ingest

Design: `docs/superpowers/specs/2026-10-01-agenticos-design.md`
````

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add SOUL.md AGENTS.md aos.yaml projects.yaml memory knowledge inbox skills README.md tests/test_seed.py
git commit -m "feat: seed content, starter skills and README"
```

---

### Task 11: Install and verify end-to-end

**Files:** none (verification only; fix and commit anything it reveals)

- [ ] **Step 1: Make `aos` available on PATH**

```bash
mkdir -p ~/.local/bin && ln -sf "$PWD/.venv/bin/aos" ~/.local/bin/aos
aos --help
```
Expected: usage listing `init, link, unlink, sync, context, serve, inbox, status, doctor`.

- [ ] **Step 2: Init and doctor**

```bash
aos init /Users/alejandrofernandez/Desktop/Projects/agenticOS
aos doctor
```
Expected: `ok    repo …`; graphskill reported `warn` (its install is currently broken: `No module named 'graphskill'`).

- [ ] **Step 3: Link a scratch project and inspect**

```bash
S=<scratchpad>/demo-project && mkdir -p "$S" && git -C "$S" init -q
aos link "$S" --name demo
cat "$S/.mcp.json" "$S/.claude/settings.local.json" "$S/.kiro/hooks/aos.json"
aos context --project "$S" | head -40
```
Expected: files as in the README table; context shows SOUL, rules, protocol, memories.

- [ ] **Step 4: MCP smoke over stdio**

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}' '{"jsonrpc":"2.0","method":"notifications/initialized"}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | aos serve --project "$S" | head -c 600
```
Expected: JSON responses including `memory_read`.

- [ ] **Step 5: Claude Code headless check**

```bash
cd "$S" && claude -p "Call the aos memory_read tool with scope project and tell me the cap." --allowedTools "mcp__aos__memory_read"
```
Expected: answer mentions 2200. (Requires approving the project `.mcp.json` server; if headless approval blocks it, record that and verify interactively.)

- [ ] **Step 6: Unlink scratch project and confirm clean**

```bash
aos unlink "$S" && ls -A "$S"
```
Expected: only `.git` and `.gitignore`-free tree (no `.aos`, `.claude`, `.kiro`, `.mcp.json`).
