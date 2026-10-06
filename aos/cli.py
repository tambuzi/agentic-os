"""`aos` command line: for humans and hooks. Agents use the MCP server instead."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from . import inbox
from .board import Board
from .config import aos_home, data_root, load_user_config, repo_root, save_user_config, slug_for_path
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
    if a.data_dir:
        data = Path(a.data_dir).expanduser().resolve()
        if data == repo or repo in data.parents:
            raise AosError("data dir must be outside the agenticOS repo", "business data must never be pushed")
        cfg["data_dir"] = str(data)
    save_user_config(cfg)
    data = data_root(cfg)
    for d in ("memory/projects", "knowledge", "inbox"):
        (data / d).mkdir(parents=True, exist_ok=True)
    print(f"agenticOS repo: {repo}\nuser state:     {aos_home()}\nlocal data:     {data_root(cfg)}")
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
    run_server(a.project, a.task)
    return 0


def cmd_inbox(a) -> int:
    aos = AOS(repo_root())
    if a.action == "list":
        items = inbox.list_proposals(aos.data)
        if not items:
            print("inbox empty")
        for p in items:
            print(f"{p['id']}  {p['kind']}.{p['op']}  {p.get('source') or '-'}  {p.get('reason') or ''}")
    elif a.action == "show":
        print(yaml.safe_dump(inbox.load(aos.data, a.id), sort_keys=False, allow_unicode=True), end="")
    elif a.action == "apply":
        aos.apply_proposal(a.id)
        print(f"applied {a.id}")
    else:
        aos.reject_proposal(a.id)
        print(f"rejected {a.id}")
    return 0


def cmd_status(a) -> int:
    repo = repo_root()
    r = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--", "skills"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise AosError("git status failed", r.stderr.strip())
    print(r.stdout.rstrip() or "no uncommitted skill changes")
    data = AOS(repo).data
    print(f"local data: {data}")
    print(f"inbox: {len(inbox.list_proposals(data))} proposal(s)")
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
    from . import codegraph
    for slug, info in sorted(cfg["projects"].items()):
        p = Path(info["path"])
        line((p / ".aos/manifest.json").exists(), f"project {slug}: {p}")
        if not codegraph.graph_server(p):
            line(False, f"graphskill {slug}: no graphskill server in .mcp.json (install graphskill, re-run aos link)")
            continue
        try:
            n = len(codegraph.list_tools(p, timeout=30))
            line(True, f"graphskill {slug}: {n} tools")
        except AosError as e:
            line(False, f"graphskill {slug}: {e.message}")
    line(shutil.which("claude") is not None,
         "claude CLI (board workers)" if shutil.which("claude") else "claude CLI not found (needed for claude workers)")
    kiro_cli = shutil.which("kiro-cli")
    line(bool(kiro_cli), "kiro-cli (board workers)" if kiro_cli else "kiro-cli not found (needed only for kiro workers)")
    if kiro_cli:
        line(bool(os.environ.get("KIRO_API_KEY")), "KIRO_API_KEY set" if os.environ.get("KIRO_API_KEY")
             else "KIRO_API_KEY not set (headless Kiro needs it unless you are logged in)")
    return 0


def _board_ctx():
    aos = AOS(repo_root())
    return aos, Board(aos.data, aos.settings["board"]["max_tasks_per_feature"])


def _check_projects(aos: AOS, names: list[str]) -> None:
    known = aos.projects()
    missing = [n for n in names if n not in known]
    if missing:
        raise AosError(f"unknown project(s): {', '.join(missing)}", "link them first with aos link")


def cmd_feature(a) -> int:
    aos, board = _board_ctx()
    if a.action == "new":
        projects = [p.strip() for p in (a.projects or "").split(",") if p.strip()]
        _check_projects(aos, projects)
        brief = f"# {a.title}\n\n## Goal\n\n## Projects\n" + "".join(f"- {p}\n" for p in projects)
        contract = "# Contract\n\nAPIs, events and schemas shared between the projects.\n"
        board.create_feature(a.slug, a.title, brief, contract)
        d = board.feature_dir(a.slug)
        print(f"feature {a.slug} created\n  edit: {d / 'brief.md'}\n  edit: {d / 'contract.md'}")
        print(f"then: aos task add {a.slug} <project> \"<title>\" [--after ids]   or ask the agent to deliver it (aos-feature skill)")
    elif a.action == "list":
        for f in board.features():
            n = len(board.tasks(feature=f["slug"]))
            print(f"{f['slug']:<24} {f['status']:<10} contract v{f['contract_version']}  {n} task(s)  {f['title']}")
    elif a.action == "show":
        f = board.feature(a.slug)
        print(f"# {f['slug']}: {f['title']} [{f['status']}]\n")
        print(board.brief(a.slug).strip() or "(no brief)")
        c = board.contract(a.slug)
        print(f"\n## Contract v{c['version']}\n\n{c['text'].strip() or '(empty)'}\n")
        for t in board.tasks(feature=a.slug):
            print(f"#{t['id']:<4} {t['project']:<14} {t['status']:<10} {t['title']}")
        for p in board.proposals(a.slug, "pending"):
            print(f"\nproposal #{p['id']} (task #{p['task']}): {p['reason']}\n{p['body']}")
    elif a.action == "approve":
        new = None
        if a.edit:
            p = next((x for x in board.proposals(status="pending") if x["id"] == a.id), None)
            if not p:
                raise AosError(f"no pending proposal #{a.id}")
            contract = board.contract(p["feature"])
            draft = (f"{contract['text'].rstrip()}\n\n## Change v{contract['version'] + 1} "
                     f"(proposal #{p['id']})\n\n{p['body']}\n")
            tmp = board.data / f".proposal-{a.id}.md"
            tmp.write_text(draft)
            try:
                code = subprocess.call([*shlex.split(os.environ.get("EDITOR") or "vi"), str(tmp)])
                new = tmp.read_text()
            finally:
                tmp.unlink(missing_ok=True)
            if code != 0:
                raise AosError(f"editor exited with code {code}; proposal not approved")
            if not new.strip():
                raise AosError("edited contract is empty; proposal not approved")
        r = board.approve(a.id, new_contract=new)
        print(f"approved #{a.id}: contract is now v{r['contract_version']}")
    elif a.action == "reject":
        board.reject(a.id, a.reason or "")
        print(f"rejected #{a.id}")
    elif a.action == "workflow":
        from .kiroflow import write_recipe
        path = write_recipe(board, a.slug, Path(a.out).expanduser().resolve(), shutil.which("aos") or "aos", a.poll)
        print(f"wrote {path}\nrun it from Kiro's Workflows panel (aos-{a.slug}); it starts the board itself")
    else:
        board.set_feature_status(a.slug, "done" if a.action == "done" else "cancelled")
        print(f"feature {a.slug} {a.action}")
    return 0


def cmd_task(a) -> int:
    from .workers.common import profile_settings, resolve_profile
    aos, board = _board_ctx()
    if a.action == "add":
        _check_projects(aos, [a.project])
        profile = resolve_profile(aos, a.project, a.worker)
        attempts = profile_settings(aos, profile, a.project)["max_attempts"]
        spec = Path(a.spec_file).read_text() if a.spec_file else ""
        deps = a.after or []
        tid = board.add_task(a.feature, a.project, a.title, spec, deps, worker=profile, max_attempts=attempts)
        print(f"task #{tid} added ({a.project}, worker {profile})")
    elif a.action == "list":
        for t in board.tasks(feature=a.feature, status=a.status):
            print(f"#{t['id']:<4} {t['feature']:<18} {t['project']:<14} {t['status']:<10} {t['worker']:<7} {t['title']}")
    elif a.action == "show":
        t = board.task(a.id)
        print(yaml.safe_dump({k: t[k] for k in ("id", "feature", "project", "title", "status", "worker",
                                                 "attempts", "max_attempts", "depends_on", "result", "note")},
                             sort_keys=False, allow_unicode=True), end="")
        if t["spec"]:
            print(f"spec:\n{t['spec']}")
        if a.log:
            logs = sorted((board.data / "logs").glob(f"{t['id']}-*.log"), key=lambda p: p.stat().st_mtime)
            if logs:
                print(f"--- {logs[-1]} (last 60 lines)")
                print("\n".join(logs[-1].read_text(errors="replace").splitlines()[-60:]))
            else:
                print("(no log yet)")
    elif a.action == "retry":
        board.retry(a.id, note=a.note, worker=a.worker, resume=a.resume)
        print(f"task #{a.id} queued again")
    elif a.action == "cancel":
        board.cancel(a.id)
        print(f"task #{a.id} cancelled")
    else:
        board.unblock(a.id, a.note or "")
        print(f"task #{a.id} unblocked")
    return 0


def _watch_poll(a, cursor) -> dict:
    """One poll of the Kiro workflow `command` watch handler.

    A Kiro watch node completes on its first `new-activity`, so: `idle` while the task
    just runs or waits; `new-activity` only when a human is needed (aos.attention) and
    that situation changed since the cursor; `terminal-state` when done or cancelled."""
    from .attention import TERMINAL, task_attention
    if a.demo:
        n = int((cursor or {}).get("n", 0)) + 1
        if n >= a.demo:
            return {"outcome": "terminal-state", "cursor": {"n": n}, "payload": json.dumps({"polls": n})}
        return {"outcome": "new-activity", "cursor": {"n": n}, "payload": json.dumps({"polls": n})}
    if a.task is None:
        raise AosError("board watch needs --task <id> or --demo <n>")
    _, board = _board_ctx()
    t = board.task(a.task)
    sig, info = task_attention(board, t, board.stuck())
    if t["status"] in TERMINAL:
        return {"outcome": "terminal-state", "cursor": {"sig": t["status"]}, "payload": json.dumps(info)}
    outcome = "new-activity" if sig and sig != (cursor or {}).get("sig") else "idle"
    return {"outcome": outcome, "cursor": {"sig": sig}, "payload": json.dumps(info)}


def cmd_board_watch(a) -> int:
    """Kiro `command` watch handler: JSON on stdin, exactly one JSON object on stdout, always."""
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        request = {}
    cursor = request.get("cursor") if isinstance(request, dict) else None
    try:
        out = _watch_poll(a, cursor)
    except Exception as e:  # never break the handler contract
        msg = e.message if isinstance(e, AosError) else f"{type(e).__name__}: {e}"
        out = {"outcome": "terminal-state", "cursor": cursor or {}, "payload": json.dumps({"error": msg})}
    print(json.dumps(out))
    return 0


def cmd_board(a) -> int:
    if a.action == "watch":
        return cmd_board_watch(a)
    if a.action == "run":
        from .dispatcher import Dispatcher
        repo = repo_root()
        print("dispatching (Ctrl-C to stop launching; twice to stop workers)" if not a.once else "dispatching once")
        Dispatcher(repo, feature=a.feature, parallel=a.parallel).run(once=a.once, until_done=a.until_done)
        return 0
    from .dispatcher import dispatcher_status
    _, board = _board_ctx()
    st = dispatcher_status(board.data)
    print(f"dispatcher: running (pid {st['pid']}, feature {st.get('feature') or 'all'})" if st["running"]
          else "dispatcher: not running")
    stuck = board.stuck()
    for f in board.features():
        if a.feature and f["slug"] != a.feature:
            continue
        print(f"{f['slug']} [{f['status']}] contract v{f['contract_version']}: {f['title']}")
        for t in board.tasks(feature=f["slug"]):
            extra = ""
            if t["id"] in stuck:
                extra = "  waiting on failed/cancelled " + ", ".join(f"#{d}" for d in stuck[t["id"]])
            elif t["status"] == "blocked":
                blk = [e for e in board.events(f["slug"]) if e["task"] == t["id"] and e["kind"] == "blocker"]
                extra = f"  {blk[-1]['body'][:80]}" if blk else ""
            print(f"  #{t['id']:<4} {t['project']:<14} {t['status']:<10} {t['attempts']}/{t['max_attempts']}  {t['title']}{extra}")
        for p in board.proposals(f["slug"], "pending"):
            print(f"  proposal #{p['id']} pending: {p['reason']}  (aos feature approve|reject {p['id']})")
    return 0


def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def _id_list(value: str) -> list[int]:
    try:
        return [int(x) for x in value.split(",") if x.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("comma-separated task ids, e.g. 1,2")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aos", description="agenticOS: shared brain for Claude Code and Kiro")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="point this machine at an agenticOS repo")
    s.add_argument("repo", nargs="?", default=".")
    s.add_argument("--graphskill", help="command that runs graphskill, e.g. '/path/.venv/bin/python -m graphskill'")
    s.add_argument("--data-dir", help="local business data dir (default: ~/.agenticos/data); never inside the repo")
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

    s = sub.add_parser("context", help="print session context (SessionStart hook)")
    s.add_argument("--project", default=".")
    s.set_defaults(fn=cmd_context)

    s = sub.add_parser("serve", help="run the MCP server over stdio")
    s.add_argument("--project", default=".")
    s.add_argument("--task", type=int, help="board worker mode for this task id")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("inbox", help="review staged memory/skill proposals")
    isub = s.add_subparsers(dest="action", required=True)
    isub.add_parser("list")
    for action in ("show", "apply", "reject"):
        isub.add_parser(action).add_argument("id")
    s.set_defaults(fn=cmd_inbox)

    s = sub.add_parser("feature", help="multi-project features")
    fsub = s.add_subparsers(dest="action", required=True)
    f = fsub.add_parser("new")
    f.add_argument("slug")
    f.add_argument("--title", required=True)
    f.add_argument("--projects", help="comma-separated linked project slugs")
    fsub.add_parser("list")
    for name in ("show", "done", "cancel"):
        fsub.add_parser(name).add_argument("slug")
    f = fsub.add_parser("approve")
    f.add_argument("id", type=int)
    f.add_argument("--edit", action="store_true", help="edit the new contract in $EDITOR")
    f = fsub.add_parser("workflow", help="write a Kiro workflow that runs this feature on the board")
    f.add_argument("slug")
    f.add_argument("--out", default=".", help="workspace to write .kiro/workflows/ into (default: here)")
    f.add_argument("--poll", type=int, default=30, help="watch poll interval in seconds (min 10)")
    f = fsub.add_parser("reject")
    f.add_argument("id", type=int)
    f.add_argument("--reason")
    s.set_defaults(fn=cmd_feature)

    s = sub.add_parser("task", help="board tasks")
    tsub = s.add_subparsers(dest="action", required=True)
    t = tsub.add_parser("add")
    t.add_argument("feature")
    t.add_argument("project")
    t.add_argument("title")
    t.add_argument("--spec-file")
    t.add_argument("--after", type=_id_list, help="comma-separated task ids this task waits for")
    t.add_argument("--worker", choices=None, help="worker profile (claude, kiro, ...)")
    t = tsub.add_parser("list")
    t.add_argument("--feature")
    t.add_argument("--status")
    t = tsub.add_parser("show")
    t.add_argument("id", type=int)
    t.add_argument("--log", action="store_true")
    t = tsub.add_parser("retry")
    t.add_argument("id", type=int)
    t.add_argument("--resume", action="store_true")
    t.add_argument("--note")
    t.add_argument("--worker")
    tsub.add_parser("cancel").add_argument("id", type=int)
    t = tsub.add_parser("unblock")
    t.add_argument("id", type=int)
    t.add_argument("--note")
    s.set_defaults(fn=cmd_task)

    s = sub.add_parser("board", help="board status, or `board run` to dispatch workers")
    s.add_argument("action", nargs="?", choices=["run", "watch"])
    s.add_argument("--task", type=int, help="watch: the task to follow (Kiro workflow watch handler)")
    s.add_argument("--demo", type=int, help="watch: spike mode, terminal after N polls")
    s.add_argument("--feature")
    s.add_argument("--parallel", type=_positive_int)
    s.add_argument("--once", action="store_true", help="one round: launch what is ready, wait for it, exit")
    s.add_argument("--until-done", action="store_true",
                   help="keep dispatching, exit when nothing more can run (finished, or waiting on a human)")
    s.set_defaults(fn=cmd_board)
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
