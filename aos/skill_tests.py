"""Skill compliance tests: scenario files next to each skill, run headless, checked with fixed rules.

A test file lives at `skills/<name>/tests/<scenario>.yaml` (spec 2026-11-01 §3). This module reads
and validates the files, pulls the tool calls out of a Claude `stream-json` transcript, and
evaluates each check against them. No AI judges anything here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .errors import AosError

TOOLS = ("claude", "any")
CHECK_KEYS = {"calls": {"at_least", "at_most", "distinct", "where"}, "before": set(), "never": set()}


class TestFileError(AosError):
    __test__ = False  # not a pytest test class


@dataclass
class Scenario:
    path: Path
    skill: str
    name: str
    tool: str
    projects: list[str]
    turns: list[str]
    timeout_min: float
    checks: list[dict] = field(default_factory=list)

    @property
    def draft(self) -> bool:
        return self.path.stem.startswith("draft-")


def _bad(path, why: str) -> TestFileError:
    return TestFileError(f"{path}: {why}", "see the check vocabulary in README (Skill compliance tests)")


def _strs(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)


def _count(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _check(path, c) -> dict:
    if not isinstance(c, dict):
        raise _bad(path, f"a check must be a mapping, got {c!r}")
    kinds = [k for k in c if k in CHECK_KEYS]
    unknown = [k for k in c if k not in CHECK_KEYS and not any(k in CHECK_KEYS[x] for x in kinds)]
    if unknown or len(kinds) != 1:
        raise _bad(path, f"unknown check {', '.join(map(str, unknown)) or c!r}: use calls, before or never")
    kind = kinds[0]
    extra = set(c) - {kind} - CHECK_KEYS[kind]
    if extra:
        raise _bad(path, f"{kind} does not take {', '.join(sorted(map(str, extra)))}")
    if kind == "before":
        if not (_strs(c["before"]) and len(c["before"]) == 2):
            raise _bad(path, "before needs two tool names: [a, b]")
        return {**c, "before": [_strip(x) for x in c["before"]]}
    if not (isinstance(c[kind], str) and c[kind].strip()):
        raise _bad(path, f"{kind} needs a tool name")
    for k in ("at_least", "at_most"):
        if k in c and not _count(c[k]):
            raise _bad(path, f"{k} must be a whole number ≥ 0, got {c[k]!r}")
    if "distinct" in c and not (isinstance(c["distinct"], str) and c["distinct"]):
        raise _bad(path, "distinct needs an argument name")
    if "where" in c and not isinstance(c["where"], dict):
        raise _bad(path, "where must be a mapping of argument: value")
    return {**c, kind: _strip(c[kind])}  # mcp__aos__task_create in a check matches task_create


def load(path) -> Scenario:
    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise _bad(path, f"not valid yaml: {e}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise _bad(path, f"can't read it: {e}") from None
    if not isinstance(doc, dict):
        raise _bad(path, "a test file is a mapping with scenario, turns and checks")
    for k in ("scenario", "turns", "checks"):
        if k not in doc:
            raise _bad(path, f"missing {k}")
    if not (isinstance(doc["scenario"], str) and doc["scenario"].strip()):
        raise _bad(path, "scenario must be a name")
    if not (_strs(doc["turns"]) and doc["turns"]):
        raise _bad(path, "turns must be a list of what the user says")
    tool = doc.get("tool", "claude")
    if tool not in TOOLS:
        raise _bad(path, f"tool must be one of {', '.join(TOOLS)}, got {tool!r}")
    projects = doc.get("projects", ["demo"])
    if not (_strs(projects) and projects):
        raise _bad(path, "projects must be a list of names")
    timeout = doc.get("timeout_min", 6)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise _bad(path, f"timeout_min must be a positive number, got {timeout!r}")
    if not (isinstance(doc["checks"], list) and doc["checks"]):
        raise _bad(path, "checks must be a non-empty list")
    checks = [_check(path, c) for c in doc["checks"]]
    return Scenario(path=path, skill=path.parent.parent.name, name=doc["scenario"], tool=tool,
                    projects=list(projects), turns=list(doc["turns"]), timeout_min=float(timeout), checks=checks)


def discover(repo, skill: str | None = None, include_drafts: bool = False) -> tuple[list[Scenario], list[str]]:
    """Every test file under skills/*/tests (or one skill's). Bad files are reported, not raised."""
    root = Path(repo) / "skills"
    pattern = f"{skill}/tests/*.yaml" if skill else "*/tests/*.yaml"
    ok, errors = [], []
    for p in sorted(root.glob(pattern)):
        if p.stem.startswith("draft-") and not include_drafts:
            continue
        try:
            ok.append(load(p))
        except TestFileError as e:
            errors.append(e.message)
        except Exception as e:  # a file we didn't foresee: report it, keep the others
            errors.append(f"{p}: {type(e).__name__}: {e}")
    return ok, errors


def _strip(name: str) -> str:
    # mcp__aos__task_create -> task_create; plain tools (Bash, Skill) stay as they are
    return name.split("__", 2)[2] if name.startswith("mcp__") and name.count("__") >= 2 else name


def tool_calls_from_stream(lines: list[str]) -> list[dict]:
    calls = []
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict) or ev.get("type") != "assistant":
            continue
        for block in (ev.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name"):
                calls.append({"name": _strip(block["name"]), "input": block.get("input") or {}})
    return calls


def _label(c: dict) -> str:
    if "before" in c:
        return f"before: {c['before'][0]}, {c['before'][1]}"
    kind = "calls" if "calls" in c else "never"
    opts = ", ".join(f"{k}: {v}" for k, v in c.items() if k != kind)
    return f"{kind}: {c[kind]}" + (f" ({opts})" if opts else "")


def _matching(calls, name, where=None) -> list[dict]:
    where = where or {}
    return [c for c in calls if c["name"] == name and all(c["input"].get(k) == v for k, v in where.items())]


def _first(calls, name) -> int | None:
    return next((i for i, c in enumerate(calls) if c["name"] == name), None)


def _one(c: dict, calls: list[dict]) -> tuple[bool, str]:
    if "never" in c:
        n = len(_matching(calls, c["never"]))
        return (True, f"{c['never']} never called") if n == 0 else (False, f"{c['never']} called {n}×, needed never")
    if "before" in c:
        a, b = c["before"]
        ia, ib = _first(calls, a), _first(calls, b)
        if ia is None:
            return False, f"{a} never called"
        if ib is None:
            return True, f"{b} never called"
        return (True, f"{a} before {b}") if ia < ib else (False, f"{a} came after {b}")
    name, lo, hi = c["calls"], c.get("at_least", 1), c.get("at_most")
    found = _matching(calls, name, c.get("where"))
    if "distinct" in c:
        arg = c["distinct"]
        n = len({json.dumps(x["input"].get(arg), sort_keys=True) for x in found if arg in x["input"]})
        what = f"{name}: {n} distinct {arg}"
    else:
        n = len(found)
        what = f"{name} called {n}×"
    if n < lo:
        return False, f"{what}, needed ≥ {lo}"
    if hi is not None and n > hi:
        return False, f"{what}, needed ≤ {hi}"
    return True, what


def evaluate(scenario: Scenario, calls: list[dict]) -> list[dict]:
    out = []
    for c in scenario.checks:
        ok, why = _one(c, calls)
        out.append({"check": _label(c), "ok": ok, "why": why})
    return out


# -- running (spec §3.2) --------------------------------------------------------------
REPLY_MAX = 1500  # the agent's final reply per turn, kept in the report to see why a check failed

def _sh(argv, cwd, env, what: str) -> None:
    r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise AosError(f"setup failed ({what}): {(r.stderr or r.stdout).strip()[-300:]}")


def _setup(aos, scenario: Scenario, tmp: Path, env: dict) -> list[Path]:
    aos_cmd = [sys.executable, "-m", "aos"]
    _sh([*aos_cmd, "init", str(aos.repo), "--worker", "claude"], tmp, env, "aos init")
    acp = Path(aos.home) / "acp"
    if acp.exists():  # the Claude ACP adapter, installed once per machine
        (tmp / "home" / "acp").symlink_to(acp.resolve())
    git = ["git", "-c", "user.name=aos skill test", "-c", "user.email=aos@localhost"]
    paths = []
    for name in scenario.projects:
        p = tmp / "projects" / name
        p.mkdir(parents=True)
        (p / "README.md").write_text(f"# {name}\n\nA demo project for the {scenario.skill} skill test.\n")
        _sh(["git", "init", "-q", "-b", "main"], p, env, f"git init {name}")
        _sh([*git, "add", "."], p, env, f"git add {name}")
        _sh([*git, "commit", "-qm", "init"], p, env, f"git commit {name}")
        _sh([*aos_cmd, "link", str(p), "--no-graphskill", "--targets", "claude"], tmp, env, f"aos link {name}")
        paths.append(p)
    return paths


def run_scenario(aos, scenario: Scenario, claude_cmd: list[str] | None = None, keep: bool = False) -> dict:
    """One scenario in a throwaway AOS_HOME, with workers never launched (AOS_DRY_DISPATCH=1)."""
    from .sessions import claude_project_folder
    out = {"scenario": scenario.name, "ok": False, "checks": [], "calls": [], "replies": [], "cost_usd": 0.0,
           "seconds": 0.0, "error": None, "kept": None}
    cmd = list(claude_cmd or ["claude"])
    if not shutil.which(cmd[0]):
        out["error"] = f"claude CLI not found ({cmd[0]}); install Claude Code to run skill tests"
        return out
    start = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="aos-skilltest-", dir=Path.home()))
    env = {k: v for k, v in os.environ.items() if k != "AOS_REPO"}
    # AOS_SKILL_TEST: skill writes are staged in the throwaway inbox, and the demo sessions are not
    # captured (no learner runs outliving the throwaway home)
    env.update(AOS_HOME=str(tmp / "home"), AOS_DRY_DISPATCH="1", AOS_SKILL_TEST="1")
    folders: list[str] = []
    try:
        projects = _setup(aos, scenario, tmp, env)
        folders = [claude_project_folder(p) for p in projects]
        model = ((aos.settings.get("workers") or {}).get("claude") or {}).get("model")
        deadline = start + 60 * scenario.timeout_min
        session = None
        for turn in scenario.turns:
            argv = [*cmd, "-p", turn, "--output-format", "stream-json", "--verbose",
                    "--permission-mode", "acceptEdits", "--allowedTools", "mcp__aos",  # headless: no prompt to allow them
                    *(["--model", model] if model else []),
                    *(["--resume", session] if session else [])]
            left = deadline - time.monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired(argv, 0)
            r = subprocess.run(argv, cwd=projects[0], env=env, capture_output=True, text=True, timeout=left,
                               stdin=subprocess.DEVNULL)
            lines = r.stdout.splitlines()
            out["calls"] += tool_calls_from_stream(lines)
            for line in lines:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if isinstance(ev, dict):
                    session = ev.get("session_id") or session
                    if ev.get("type") == "result":
                        out["cost_usd"] += float(ev.get("total_cost_usd") or 0)
                        out["replies"].append(str(ev.get("result") or "")[:REPLY_MAX])
            if r.returncode != 0:
                out["error"] = f"claude exited {r.returncode}: {(r.stderr or '').strip()[-300:]}"
                break
    except subprocess.TimeoutExpired:
        out["error"] = f"timed out after {scenario.timeout_min:g} min"
    except Exception as e:  # a crashed scenario fails; the others still run
        out["error"] = e.message if isinstance(e, AosError) else f"{type(e).__name__}: {e}"
    finally:
        if keep:
            out["kept"] = str(tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
            for f in folders:
                shutil.rmtree(Path.home() / ".claude" / "projects" / f, ignore_errors=True)
    out["seconds"] = round(time.monotonic() - start, 1)
    out["checks"] = evaluate(scenario, out["calls"])
    out["ok"] = out["error"] is None and all(c["ok"] for c in out["checks"])
    return out


def _report(skill: str, results: list[dict], errors: list[str]) -> str:
    passed = sum(r["ok"] for r in results)
    lines = [f"# Skill tests: {skill}", "",
             f"{passed} of {len(results)} scenarios passed, "
             f"${sum(r['cost_usd'] for r in results):.2f}, {sum(r['seconds'] for r in results):.0f}s", ""]
    for e in errors:
        lines += [f"- test file error: {e}"]
    for r in results:
        lines += ["", f"## {r['scenario']}: {'passed' if r['ok'] else 'failed'}", "",
                  f"${r['cost_usd']:.2f}, {r['seconds']:.0f}s" + (f", kept at {r['kept']}" if r["kept"] else "")]
        if r["error"]:
            lines += ["", f"**Error:** {r['error']}"]
        lines += [""] + [f"- {'✓' if c['ok'] else '✗'} `{c['check']}`: {c['why']}" for c in r["checks"]]
        lines += ["", "Tool calls:", ""] + [f"{i}. {c['name']}" for i, c in enumerate(r["calls"], 1)]
        for i, reply in enumerate(r.get("replies") or [], 1):
            lines += ["", f"Reply to turn {i}:", "", *[f"> {l}" for l in reply.splitlines() or [""]]]
    return "\n".join(lines) + "\n"


def run_skill(aos, skill: str, scenario_name: str | None = None, claude_cmd: list[str] | None = None,
              keep: bool = False) -> tuple[bool, Path]:
    scenarios, errors = discover(aos.repo, skill)
    if scenario_name:
        scenarios = [s for s in scenarios if s.name == scenario_name or s.path.stem == scenario_name]
    if not scenarios and not errors:
        raise AosError(f"no tests for skill {skill!r}" + (f" named {scenario_name!r}" if scenario_name else ""),
                       f"add skills/{skill}/tests/<scenario>.yaml, or draft one with --draft")
    results = [run_scenario(aos, s, claude_cmd, keep) for s in scenarios]
    out_dir = Path(aos.data) / "skills" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    report = out_dir / f"{skill}-{now.strftime('%Y-%m-%d-%H%M%S')}.md"
    report.write_text(_report(skill, results, errors))
    total = len(results) + len(errors)
    with open(out_dir / "results.jsonl", "a") as fh:
        fh.write(json.dumps({"skill": skill, "passed": sum(r["ok"] for r in results), "total": total,
                             "at": now.isoformat(timespec="seconds")}) + "\n")
    return (not errors and all(r["ok"] for r in results)), report


# -- drafts (spec §3.2 --draft) -----------------------------------------------------
DRAFT_PROTOCOL = """\
## Skill test drafter protocol

Write ONE compliance test for the agenticOS skill below and save it with write_test(yaml_text).
A test file is YAML:

  scenario: <short-name>
  tool: claude
  projects: [api, web]           # throwaway demo projects
  turns: ["<what the user says first>", "<what they say next>"]
  timeout_min: 6
  checks:                        # only these check types exist
    - calls: <tool>              # at_least: n / at_most: n (default at_least: 1),
      at_least: 1                #   distinct: <arg>, where: {arg: value}
    - before: [<tool a>, <tool b>]   # first a comes before first b
    - never: <tool>

Tool names are the aos MCP tool names without the mcp__aos__ prefix (task_create, feature_create,
board_start, code_query, ...) or plain Claude tools (Bash, Edit). Test what the skill says the
agent must and must not do. If write_test returns an error, fix the file and call it again."""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "scenario"


def write_draft(repo, skill: str, yaml_text: str) -> Path:
    """Validate a drafted test file and save it as skills/<skill>/tests/draft-<slug>.yaml."""
    try:
        doc = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise TestFileError(f"not valid yaml: {e}") from None
    name = doc.get("scenario") if isinstance(doc, dict) else None
    tests = Path(repo) / "skills" / skill / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    tmp = tests / f".draft-{os.getpid()}.tmp"
    tmp.write_text(yaml_text)
    try:
        load(tmp)
        slug = _slug(str(name))
        path, n = tests / f"draft-{slug}.yaml", 2
        while path.exists():
            path, n = tests / f"draft-{slug}-{n}.yaml", n + 1
        tmp.rename(path)
        return path
    finally:
        tmp.unlink(missing_ok=True)


def draft(aos, skill: str) -> Path:
    """A cheap AI run drafts one test file for the skill; the user reviews and renames it to run it."""
    from .skill_health import tool_run
    md = Path(aos.repo) / "skills" / skill / "SKILL.md"
    if not md.is_file():
        raise AosError(f"no skill {skill!r} in this repo", "draft tests for skills under skills/")
    tests = md.parent / "tests"
    before = set(tests.glob("draft-*.yaml")) if tests.exists() else set()
    problem = tool_run(aos, f"draft-{skill}", DRAFT_PROTOCOL + f"\n\n## The skill: {skill}\n\n" + md.read_text(),
                       f"Draft one compliance test for the skill {skill}. Follow the drafter protocol.",
                       ["--draft-skill", skill])
    new = sorted(set(tests.glob("draft-*.yaml")) - before) if tests.exists() else []
    if not new:
        raise AosError(f"the drafter wrote no valid test file{f' ({problem})' if problem else ''}",
                       f"see {Path(aos.data) / 'logs' / f'draft-{skill}.log'}")
    return new[0]
