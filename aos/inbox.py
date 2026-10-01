"""Proposals awaiting human approval: one YAML file each in <data>/inbox/ (local only)."""

from __future__ import annotations

import secrets
import time
from pathlib import Path

import yaml

from .errors import AosError
from .store import inside, read_text, write_atomic


def inbox_dir(root) -> Path:
    return Path(root) / "inbox"


def stage(root, kind: str, op: str, args: dict, source: str = "", reason: str = "") -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    pid = f"{ts}-{kind}-{secrets.token_hex(3)}"
    data = {"id": pid, "kind": kind, "op": op, "args": args,
            "source": source, "reason": reason, "created": ts}
    write_atomic(inbox_dir(root) / f"{pid}.yaml", yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return pid


def _path(root, pid: str) -> Path:
    p = inside(inbox_dir(root), f"{pid}.yaml")
    if not p.is_file():
        raise AosError(f"no proposal {pid!r}", "run `aos inbox list`")
    return p


def load(root, pid: str) -> dict:
    return yaml.safe_load(read_text(_path(root, pid)))


def discard(root, pid: str) -> None:
    _path(root, pid).unlink()


def list_proposals(root) -> list[dict]:
    d = inbox_dir(root)
    return [yaml.safe_load(read_text(p)) for p in sorted(d.glob("*.yaml"))] if d.is_dir() else []
