"""Atomic, lockable file access. Every write in agenticOS goes through here."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .errors import AosError


def read_text(path: str | Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _default_mode() -> int:
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def write_atomic(path: str | Path, data: str | bytes, mode: int | None = None) -> None:
    """Replace `path` atomically. Keeps the existing file's mode unless `mode` is given;
    new files get the usual umask-derived mode (mkstemp alone would leave them 0600)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if mode is None:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else _default_mode()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        os.chmod(tmp, mode)
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
