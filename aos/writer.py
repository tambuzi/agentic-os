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
