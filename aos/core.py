"""AOS: the one facade the CLI and MCP server use. Applies scopes and write modes.

`repo` is the agenticOS git repo (code, skills, SOUL/AGENTS, settings).
`data` is local business data (memory, inbox, project registry), never in the repo.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from . import inbox, skills
from .config import aos_home, check_slug, data_root, load_settings
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
    def __init__(self, repo: str | Path, home: str | Path | None = None, slug: str | None = None,
                 data: str | Path | None = None):
        self.repo = Path(repo)
        self.home = Path(home) if home else aos_home()
        self.data = Path(data) if data else data_root()
        self.slug = slug
        self.settings = load_settings(self.repo)

    # -- memory --------------------------------------------------------------
    def memory_path(self, scope: str, slug: str | None = None) -> Path:
        if scope == "org":
            return self.data / "memory" / "org.md"
        if scope == "user":
            return self.home / "user.md"
        if scope == "project":
            s = slug or self.slug
            if not s:
                raise AosError("no project in this session", "run inside a linked project (aos link)")
            check_slug(s)
            return self.data / "memory" / "projects" / f"{s}.md"
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
            pid = inbox.stage(self.data, "memory", op, payload, source, reason)
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
            pid = inbox.stage(self.data, "skill", action, {**args, "name": name}, source, reason)
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
        p = inbox.load(self.data, pid)
        args = dict(p.get("args") or {})
        if p.get("kind") == "memory":
            res = self.memory_write(args.pop("scope"), p["op"], args, direct=True)
        elif p.get("kind") == "skill":
            res = self.skill_write(p["op"], args.pop("name"), args, direct=True)
        else:
            raise AosError(f"unknown proposal kind {p.get('kind')!r}")
        inbox.discard(self.data, pid)
        return {**res, "applied": True}

    def reject_proposal(self, pid: str) -> None:
        inbox.discard(self.data, pid)

    # -- projects ------------------------------------------------------------
    def _projects_file(self) -> Path:
        return self.data / "projects.yaml"

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
