"""What every board worker gets, whichever tool runs it (spec §6.1)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..board import Board
from ..config import load_user_config
from ..context import build_context
from ..core import AOS
from ..errors import AosError
from ..store import read_text, write_atomic

WORKER_PROTOCOL = """\
You are an agenticOS board worker running headless on one task of a multi-project feature. Other workers handle the other projects in parallel.
1. Call task_show first.
2. Call board_read before each step and before finishing; follow contract changes and other workers' notes. Anything under messages_for_you comes from the user: follow it, it overrides your plan.
3. Orient with the graphskill tools (repo_map, search_symbols, callers, read_symbol_body) before reading whole files; for other projects in the feature use code_query(project, tool, arguments).
4. Work only inside the current directory (a git worktree on the feature branch). Commit with tests on the current branch. Never push. Never edit other projects.
5. If the shared contract must change, call task_propose_contract and continue with the parts it doesn't affect. If nothing is left that you can do, call task_block with the reason.
6. Finish with task_complete(summary): what changed, the commits, how it was tested, and anything dependent tasks must know."""

NEUTRAL_TOOL = re.compile(r"^(read|write|shell:\S.*|mcp:[A-Za-z0-9_-]+)$")


@dataclass
class RunSpec:
    task: dict
    project_path: Path
    worktree: Path
    run_dir: Path
    context_file: Path
    prompt: str
    mcp_servers: dict
    allowed: list[str]
    model: str | None
    resume: bool
    session_id: str | None
    role: str = "worker"  # or "reviewer"


@dataclass
class Launch:
    argv: list[str]
    cwd: Path
    env: dict
    session_id: str | None = None
    cleanup: Callable[[], None] = field(default=lambda: None)


def _project_worker(aos: AOS, project: str) -> dict:
    return ((aos.projects().get(project) or {}).get("worker")) or {}


def resolve_tool(aos: AOS, project: str, requested: str | None = None,
                 feature_worker: str | None = None) -> tuple[str, str]:
    """(profile, where it came from). Precedence: task > feature > project > the user's
    global setting (`aos worker`) > the team's board.default_worker."""
    from ..config import global_worker
    if requested:
        return requested, "task"
    if feature_worker:
        return feature_worker, "feature"
    project_profile = _project_worker(aos, project).get("profile")
    if project_profile:
        return project_profile, f"project {project}"
    mine = global_worker()
    if mine:
        return mine, "global (aos worker)"
    return aos.settings["board"]["default_worker"], "board default"


def resolve_profile(aos: AOS, project: str, requested: str | None = None,
                    feature_worker: str | None = None) -> str:
    return resolve_tool(aos, project, requested, feature_worker)[0]


def profile_settings(aos: AOS, profile: str, project: str) -> dict:
    workers = aos.settings["workers"]
    if profile == "common" or profile not in workers:
        raise AosError(f"unknown worker profile {profile!r}", "define it under workers: in aos.yaml")
    common, own = workers.get("common") or {}, workers[profile] or {}
    s = {**common, **own}
    tools = [*common.get("allowed_tools", []), *own.get("allowed_tools", []),
             *_project_worker(aos, project).get("allowed_tools", [])]
    for t in tools:
        if not NEUTRAL_TOOL.match(str(t)):
            raise AosError(f"invalid allowed tool {t!r}", "use read, write, shell:<command prefix> or mcp:<server>")
    # the project's verify command is the user's own and aos runs it anyway: workers may run it too
    verify = _project_worker(aos, project).get("verify_command")
    if verify:
        from ..acp.permissions import chain_segments
        tools += [f"shell:{c}" for c in chain_segments(str(verify)) or []]
    s["allowed_tools"] = list(dict.fromkeys(tools))
    s.setdefault("adapter", profile)
    return s


def add_allowed_tool(aos: AOS, project: str, rule: str) -> None:
    """`aos task approve --always`: remember a neutral rule in the project's local worker settings."""
    import yaml
    from ..store import locked
    path = aos.data / "projects.yaml"
    with locked(path):
        data = yaml.safe_load(read_text(path)) or {}
        proj = data.setdefault("projects", {}).setdefault(project, {}) or {}
        data["projects"][project] = proj
        worker = proj.setdefault("worker", {}) or {}
        proj["worker"] = worker
        tools = worker.setdefault("allowed_tools", [])
        if rule not in tools:
            tools.append(rule)
        write_atomic(path, yaml.safe_dump(data, sort_keys=True, allow_unicode=True))


def linked_project_path(project: str) -> Path:
    info = load_user_config()["projects"].get(project)
    if not info:
        raise AosError(f"project {project!r} is not linked on this machine", f"aos link <path> --name {project}")
    return Path(info["path"])


def mcp_servers(project_path: Path, task_id: int, aos_bin: str, generation: int | None = None,
                review: bool = False) -> dict:
    args = ["serve", "--project", str(project_path), "--task", str(task_id)]
    if generation is not None:
        args += ["--attempt", str(generation)]  # attempt token: stale workers get refused
    if review:
        args.append("--review")  # reviewer tools (review_pass / review_fail), no task_complete
    servers = {"aos": {"command": aos_bin, "args": args}}
    try:
        gs = (json.loads(read_text(Path(project_path) / ".mcp.json") or "{}").get("mcpServers") or {}).get("graphskill")
    except json.JSONDecodeError:
        gs = None
    if gs:
        servers["graphskill"] = gs
    return servers


def build_worker_context(aos: AOS, board: Board, task: dict) -> str:
    feature = board.feature(task["feature"])
    contract = board.contract(task["feature"])
    parts = [
        build_context(aos).rstrip(),
        "## Board worker protocol\n\n" + WORKER_PROTOCOL,
        f"## Feature {feature['slug']}: {feature['title']}\n\n{board.brief(feature['slug']).strip() or '(no brief)'}",
        f"## Contract v{contract['version']}\n\n{contract['text'].strip() or '(no contract yet)'}",
        f"## Your task #{task['id']} ({task['project']}): {task['title']}\n\n{(task['spec'] or '').strip() or '(no spec)'}",
    ]
    deps = board.dependency_results(task["id"])
    if deps:
        parts.append("## Results of tasks you depend on\n\n" + "\n\n".join(
            f"### #{d['id']} {d['project']}: {d['title']}\n{(d['result'] or '').strip()}" for d in deps))
    events = board.events(task["feature"], limit=30)
    if events:
        parts.append("## Recent feature timeline\n\n" + "\n".join(
            f"- [{e['id']}] {e['author']} {e['kind']}: {(e['body'] or '').splitlines()[0] if e['body'] else ''}"
            for e in events))
    return "\n\n".join(parts) + "\n"


def prepare_run(aos: AOS, board: Board, task: dict, worktree: Path, project_path: Path,
                aos_bin: str) -> tuple[RunSpec, dict]:
    settings = profile_settings(aos, task["worker"], task["project"])
    run_dir = board.data / "runs" / f"{task['id']}-{task['attempts']}"
    context_file = run_dir / "context.md"
    write_atomic(context_file, build_worker_context(aos, board, task))
    base = f"Do task #{task['id']}: {task['title']}. Follow the board worker protocol."
    note = (task.get("note") or "").strip()
    if task.get("resume") and note:
        prompt = note
    elif note:
        prompt = f"{base}\n\nNote from the human: {note}"
    else:
        prompt = base
    if task.get("feedback"):
        prompt += ("\n\nYour previous attempt was not accepted:\n" + task["feedback"].strip() +
                   "\nFix this before calling task_complete again.")
    if task.get("resume_hint"):
        prompt += ("\n\nA previous attempt of this task may have partly run before it was interrupted"
                   f" ({task.get('last_failure') or 'unknown reason'}). Before redoing anything, inspect the"
                   " current state (git status, git log, files, board_read) and continue from there;"
                   " do not repeat side effects that already happened.")
    spec = RunSpec(task=task, project_path=Path(project_path), worktree=Path(worktree), run_dir=run_dir,
                   context_file=context_file, prompt=prompt,
                   mcp_servers=mcp_servers(project_path, task["id"], aos_bin, task.get("generation")),
                   allowed=settings["allowed_tools"], model=settings.get("model"),
                   resume=bool(task.get("resume")), session_id=task.get("session_id"))
    return spec, settings


def parse_cost(log_text: str) -> float | None:
    """The run's cost in USD from its output, if the tool reports one: `claude -p
    --output-format json` ends with a result object carrying `total_cost_usd`."""
    for line in reversed(log_text.splitlines()):
        line = line.strip()
        if not (line.startswith("{") and "total_cost_usd" in line):
            continue
        try:
            value = json.loads(line).get("total_cost_usd")
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return None


def write_mcp_config(spec: RunSpec) -> Path:
    path = spec.run_dir / "mcp.json"
    write_atomic(path, json.dumps({"mcpServers": spec.mcp_servers}, indent=2) + "\n")
    return path


# -- independent review ----------------------------------------------------------
REVIEW_PROTOCOL = """\
You are an independent reviewer for one board task. Another agent did the work; you did not see its reasoning, on purpose.
1. Call task_show, then read the diff below and the changed files in full where needed.
2. Check the change against the task spec and the shared contract: correctness, edge cases, tests that really exercise the behaviour, nothing outside the task's scope, no secrets.
3. Run the project's tests and any quick checks you need. Do NOT edit files, commit, or push: you only judge.
4. Finish with exactly one verdict:
   - review_pass(summary): the task is done as specified;
   - review_fail(findings): concrete, actionable findings (file:line, what is wrong, what is expected). The worker will get them for its next attempt."""

REVIEW_DIFF_MAX_CHARS = 60_000
_REVIEW_DENY = ("write", "shell:git commit", "shell:git add", "shell:git push", "shell:git reset", "shell:git checkout")


def _git_out(args: list[str], cwd: Path) -> str:
    import subprocess
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else f"(git {' '.join(args)} failed: {r.stderr.strip()})"


def build_review_context(aos: AOS, board: Board, task: dict, worktree: Path) -> str:
    feature = board.feature(task["feature"])
    contract = board.contract(task["feature"])
    base = task.get("base_commit") or "HEAD~1"
    diff = _git_out(["diff", f"{base}..HEAD"], worktree)
    if len(diff) > REVIEW_DIFF_MAX_CHARS:
        diff = diff[:REVIEW_DIFF_MAX_CHARS] + "\n…[diff truncated; read the files directly]"
    checks = [e["body"] for e in board.events(task["feature"]) if e["task"] == task["id"] and e["kind"] == "verified"]
    parts = [
        build_context(aos).rstrip(),
        "## Reviewer protocol\n\n" + REVIEW_PROTOCOL,
        f"## Feature {feature['slug']}: {feature['title']}\n\n{board.brief(feature['slug']).strip() or '(no brief)'}",
        f"## Contract v{contract['version']}\n\n{contract['text'].strip() or '(no contract yet)'}",
        f"## Task #{task['id']} ({task['project']}): {task['title']}\n\n{(task['spec'] or '').strip() or '(no spec)'}",
        f"## What the worker claims\n\n{(task.get('result') or '').strip() or '(no summary)'}",
        "## Automatic checks\n\n" + ("\n".join(checks[-3:]) or "(none recorded)"),
        f"## Commits of this task ({base}..HEAD)\n\n{_git_out(['log', '--oneline', f'{base}..HEAD'], worktree).strip()}",
        f"## Diff\n\n```diff\n{diff.strip()}\n```",
    ]
    return "\n\n".join(parts) + "\n"


def prepare_review(aos: AOS, board: Board, task: dict, worktree: Path, project_path: Path,
                   aos_bin: str) -> tuple[RunSpec, dict]:
    """Inputs for the reviewer: same tool as the worker, fresh session, read-only tools."""
    settings = profile_settings(aos, task["worker"], task["project"])
    allowed = [a for a in settings["allowed_tools"] if not any(a == d or a.startswith(d) for d in _REVIEW_DENY)]
    for needed in ("read", "shell:git diff", "shell:git log", "shell:git show"):
        if needed not in allowed:
            allowed.append(needed)
    settings = {**settings, "allowed_tools": allowed}
    run_dir = board.data / "runs" / f"{task['id']}-review-{task.get('review_attempts', 0)}"
    context_file = run_dir / "context.md"
    write_atomic(context_file, build_review_context(aos, board, task, Path(worktree)))
    prompt = f"Review task #{task['id']}: {task['title']}. Follow the reviewer protocol and end with one verdict."
    spec = RunSpec(task=task, project_path=Path(project_path), worktree=Path(worktree), run_dir=run_dir,
                   context_file=context_file, prompt=prompt,
                   mcp_servers=mcp_servers(project_path, task["id"], aos_bin, task.get("generation"), review=True),
                   allowed=allowed, model=settings.get("model"), resume=False, session_id=None, role="reviewer")
    return spec, settings
