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
