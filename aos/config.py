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
}

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def aos_home() -> Path:
    return Path(os.environ.get("AOS_HOME", Path.home() / ".agenticos")).expanduser()


def data_root(cfg: dict | None = None) -> Path:
    """Local business data (memory, knowledge, inbox, project registry).

    Never inside the agenticOS git repo, so it can't be pushed. Default
    $AOS_HOME/data; override with `data_dir` in config.yaml.
    """
    raw = (cfg if cfg is not None else load_user_config()).get("data_dir")
    return Path(raw).expanduser().resolve() if raw else aos_home() / "data"


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


def check_slug(slug: str, kind: str = "project") -> None:
    if not SLUG_RE.match(slug or ""):
        raise AosError(f"invalid {kind} slug {slug!r}", "lowercase a-z, 0-9 and '-', max 64 chars")


def graphskill_cmd(cfg: dict | None = None) -> list[str] | None:
    cmd = (cfg if cfg is not None else load_user_config()).get("graphskill_cmd")
    if cmd:
        return list(cmd)
    exe = shutil.which("graphskill")
    return [exe] if exe else None
