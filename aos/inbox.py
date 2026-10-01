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
