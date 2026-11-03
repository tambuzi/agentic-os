"""Skill compliance tests: scenario files next to each skill, run headless, checked with fixed rules.

A test file lives at `skills/<name>/tests/<scenario>.yaml` (spec 2026-11-01 §3). This module reads
and validates the files, pulls the tool calls out of a Claude `stream-json` transcript, and
evaluates each check against them. No AI judges anything here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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
        raise _bad(path, f"unknown check {', '.join(unknown) or c!r}: use calls, before or never")
    kind = kinds[0]
    extra = set(c) - {kind} - CHECK_KEYS[kind]
    if extra:
        raise _bad(path, f"{kind} does not take {', '.join(sorted(extra))}")
    if kind == "before":
        if not (_strs(c["before"]) and len(c["before"]) == 2):
            raise _bad(path, "before needs two tool names: [a, b]")
        return c
    if not (isinstance(c[kind], str) and c[kind].strip()):
        raise _bad(path, f"{kind} needs a tool name")
    for k in ("at_least", "at_most"):
        if k in c and not _count(c[k]):
            raise _bad(path, f"{k} must be a whole number ≥ 0, got {c[k]!r}")
    if "distinct" in c and not (isinstance(c["distinct"], str) and c["distinct"]):
        raise _bad(path, "distinct needs an argument name")
    if "where" in c and not isinstance(c["where"], dict):
        raise _bad(path, "where must be a mapping of argument: value")
    return c


def load(path) -> Scenario:
    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise _bad(path, f"not valid yaml: {e}") from None
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
