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
from .writer import LinkContext, Writer, check_json_shape

TARGETS = {"claude": claude, "kiro": kiro}


def _check_targets(targets) -> list[str]:
    targets = list(targets)
    bad = sorted(set(targets) - set(TARGETS))
    if bad or not targets:
        raise AosError(f"unknown targets: {', '.join(bad) or '(none)'}", "use claude, kiro or claude,kiro")
    return targets


def _preflight(project: Path, targets: list[str]) -> None:
    """Validate every JSON file a render will touch, so a bad file aborts before any write."""
    for rel in sorted({f for t in targets for f in TARGETS[t].JSON_FILES}):
        text = read_text(project / rel)
        if text.strip():
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise AosError(f"cannot parse {rel}: {e}", "fix or remove the file, then re-run")
            check_json_shape(rel, data)


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
    _preflight(project, targets)
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
