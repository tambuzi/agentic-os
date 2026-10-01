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
