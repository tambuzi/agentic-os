"""The skill compliance test runner, dry dispatch and `aos skills test` (plan Task 5)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from aos import skill_tests
from aos.cli import main
from aos.core import AOS

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).parent
FAKE = [sys.executable, str(HERE / "fake_claude_stream.py")]
STREAM = HERE / "fixtures" / "skill_tests" / "plan.stream.jsonl"
GOOD = {"scenario": "plan-two-projects", "projects": ["api", "web"],
        "turns": ["Deliver a feature in api and web.", "Approved."], "timeout_min": 1,
        "checks": [{"calls": "code_query"}, {"before": ["code_query", "task_create"]},
                   {"calls": "task_create", "at_least": 2, "distinct": "project"}, {"calls": "board_start"},
                   {"never": "task_complete"}]}


def put(repo, name, doc, skill="aos-feature"):
    p = repo / "skills" / skill / "tests" / f"{name}.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc if isinstance(doc, str) else yaml.safe_dump(doc))
    return p


@pytest.fixture
def env(configured, data, tmp_path, monkeypatch, make_skill, git_repo):
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("PYTHONPATH", str(ROOT) + ":" + str(HERE))
    monkeypatch.setenv("FAKE_CLAUDE_STREAM", str(STREAM))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude.log"))
    make_skill("aos-feature")
    return AOS(configured), data, user, tmp_path


def calls_log(tmp_path):
    return [json.loads(l) for l in (tmp_path / "claude.log").read_text().splitlines()]


def test_run_scenario_passes_and_writes_the_report_and_results(env):
    aos, data, user, tmp_path = env
    put(aos.repo, "plan-two-projects", GOOD)
    ok, report = skill_tests.run_skill(aos, "aos-feature", claude_cmd=FAKE)
    assert ok
    text = report.read_text()
    assert report.parent == data / "skills" / "tests" and report.name.startswith("aos-feature-")
    assert "plan-two-projects: passed" in text and "task_create" in text and "$0.15" in text
    [row] = [json.loads(l) for l in (data / "skills" / "tests" / "results.jsonl").read_text().splitlines()]
    assert (row["skill"], row["passed"], row["total"]) == ("aos-feature", 1, 1)
    first, second = calls_log(tmp_path)
    assert "--resume" not in first["argv"] and second["argv"][second["argv"].index("--resume") + 1] == "sess-1"
    assert {"stream-json", "--verbose", "acceptEdits"} <= set(first["argv"])
    assert Path(first["cwd"]).name == "api" and first["AOS_DRY_DISPATCH"] == "1"


def test_failed_check_fails_the_run_with_its_reason(env):
    aos, data, user, tmp_path = env
    put(aos.repo, "plan-two-projects", {**GOOD, "checks": [{"calls": "task_create", "at_least": 3}]})
    ok, report = skill_tests.run_skill(aos, "aos-feature", claude_cmd=FAKE)
    assert not ok
    assert "task_create called 2×, needed ≥ 3" in report.read_text()


def test_timeout_fails_the_scenario_and_others_still_run(env):
    aos, data, user, tmp_path = env
    put(aos.repo, "a-hangs", {**GOOD, "scenario": "a-hangs", "turns": ["SLEEP please"], "timeout_min": 0.03})
    put(aos.repo, "b-plan", {**GOOD, "scenario": "b-plan"})
    ok, report = skill_tests.run_skill(aos, "aos-feature", claude_cmd=FAKE)
    text = report.read_text()
    assert not ok and "a-hangs: failed" in text and "timed out" in text and "b-plan: passed" in text


def test_dry_dispatch_launches_nothing(configured, data, tmp_path, git_repo, monkeypatch, capsys):
    from aos.config import load_user_config, save_user_config
    from aos.mcp_server import BoardTools
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(git_repo(tmp_path / "api")),
                                                                 "targets": ["claude"]}}})
    AOS(configured).register_project("api")
    aos = AOS(configured)
    monkeypatch.setenv("AOS_DRY_DISPATCH", "1")
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: launched.append(a))
    bt = BoardTools(aos)
    bt.feature_create("currency", "Currency")
    bt.task_create("api", "add it", feature="currency")
    r = bt.board_start("currency")
    assert r == {"started": False, "dry_run": True, "message": "would start the dispatcher"} and launched == []
    assert any("would start the dispatcher" in (e.get("body") or "") for e in bt.board.events("currency"))
    assert main(["board", "run", "--feature", "currency"]) == 0
    assert "would start the dispatcher" in capsys.readouterr().out and launched == []


def test_runner_uses_a_throwaway_home(env):
    aos, data, user, tmp_path = env
    put(aos.repo, "plan-two-projects", GOOD)
    before = sorted(p.relative_to(data) for p in data.rglob("*"))
    skill_tests.run_skill(aos, "aos-feature", claude_cmd=FAKE)
    [first, _] = calls_log(tmp_path)
    assert first["AOS_HOME"] != str(aos.home) and Path(first["AOS_HOME"]).is_relative_to(user)
    assert not Path(first["AOS_HOME"]).exists()                                   # removed afterwards
    assert not any((user / ".claude" / "projects").iterdir())                    # demo transcripts removed
    after = sorted(p.relative_to(data) for p in data.rglob("*") if "skills" not in p.parts)
    assert after == [p for p in before if "skills" not in p.parts]               # only the report and results


def test_keep_leaves_the_throwaway_home(env):
    aos, data, user, tmp_path = env
    put(aos.repo, "plan-two-projects", GOOD)
    skill_tests.run_skill(aos, "aos-feature", claude_cmd=FAKE, keep=True)
    assert Path(calls_log(tmp_path)[0]["AOS_HOME"]).exists()


def test_cli_validate_exit_codes(env, capsys):
    aos, data, user, tmp_path = env
    put(aos.repo, "plan-two-projects", GOOD)
    assert main(["skills", "test", "--validate"]) == 0
    assert "1 test file ok" in capsys.readouterr().out
    put(aos.repo, "broken", {"scenario": "broken"})
    assert main(["skills", "test", "--validate"]) == 1
    assert "broken.yaml: missing turns" in capsys.readouterr().out


def test_missing_claude_is_a_clear_error(env):
    aos, data, user, tmp_path = env
    put(aos.repo, "plan-two-projects", GOOD)
    ok, report = skill_tests.run_skill(aos, "aos-feature", claude_cmd=["no-such-claude-binary"])
    assert not ok and "claude CLI not found" in report.read_text()
