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
