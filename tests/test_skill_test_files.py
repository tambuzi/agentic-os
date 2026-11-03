"""Skill compliance test files and their deterministic checks (plan Task 4)."""

from pathlib import Path

import pytest
import yaml

from aos import skill_tests
from aos.skill_tests import Scenario, TestFileError

FIXTURE = Path(__file__).parent / "fixtures" / "skill_tests" / "plan.stream.jsonl"
GOOD = {"scenario": "plan-two-projects", "tool": "claude", "projects": ["api", "web"],
        "turns": ["Deliver a feature.", "Approved."], "timeout_min": 6,
        "checks": [{"calls": "code_query", "at_least": 1}, {"before": ["code_query", "task_create"]},
                   {"calls": "task_create", "at_least": 2, "distinct": "project"}, {"never": "task_complete"}]}


def put(repo, skill, name, doc):
    p = repo / "skills" / skill / "tests" / f"{name}.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(doc if isinstance(doc, str) else yaml.safe_dump(doc))
    return p


def test_load_and_validate(tmp_path):
    s = skill_tests.load(put(tmp_path, "aos-feature", "plan-two-projects", GOOD))
    assert (s.skill, s.name, s.tool, s.projects, s.timeout_min) == ("aos-feature", "plan-two-projects", "claude",
                                                                    ["api", "web"], 6)
    assert len(s.turns) == 2 and len(s.checks) == 4
    bad = [({k: v for k, v in GOOD.items() if k != "turns"}, "turns"),
           ({**GOOD, "checks": [{"sometimes": "task_create"}]}, "sometimes"),
           ({**GOOD, "turns": "just one string"}, "turns"),
           ({**GOOD, "checks": [{"calls": "x", "at_least": "two"}]}, "at_least"),
           ({**GOOD, "tool": "kiro"}, "tool"),
           ("scenario: [unclosed", "yaml")]
    for doc, word in bad:
        with pytest.raises(TestFileError, match=word):
            skill_tests.load(put(tmp_path, "aos-feature", "bad", doc))


def test_discover_skips_drafts(tmp_path):
    put(tmp_path, "aos-feature", "plan-two-projects", GOOD)
    put(tmp_path, "aos-feature", "draft-maybe", {**GOOD, "scenario": "draft-maybe"})
    put(tmp_path, "aos-memory", "saves", {**GOOD, "scenario": "saves"})
    ok, errors = skill_tests.discover(tmp_path, "aos-feature")
    assert [s.name for s in ok] == ["plan-two-projects"] and errors == []
    ok, _ = skill_tests.discover(tmp_path, include_drafts=True)
    assert sorted(s.name for s in ok) == ["draft-maybe", "plan-two-projects", "saves"]


def test_bad_files_are_reported_and_others_run(tmp_path):
    put(tmp_path, "aos-feature", "plan-two-projects", GOOD)
    bad = put(tmp_path, "aos-feature", "broken", {"scenario": "broken"})
    ok, errors = skill_tests.discover(tmp_path)
    assert [s.name for s in ok] == ["plan-two-projects"]
    assert len(errors) == 1 and str(bad) in errors[0]


def test_tool_calls_from_a_recorded_stream():
    calls = skill_tests.tool_calls_from_stream(FIXTURE.read_text().splitlines())
    assert [c["name"] for c in calls] == ["Skill", "code_query", "feature_create", "task_create", "task_create",
                                          "board_start"]
    assert calls[3]["input"] == {"project": "api", "title": "add currency"}


def scenario(checks):
    return Scenario(path=Path("x.yaml"), skill="s", name="n", tool="claude", projects=[], turns=["t"],
                    timeout_min=1, checks=checks)


CALLS = skill_tests.tool_calls_from_stream(FIXTURE.read_text().splitlines())


@pytest.mark.parametrize("check,ok,why", [
    ({"calls": "task_create", "at_least": 2}, True, "task_create called 2×"),
    ({"calls": "task_create", "at_least": 3}, False, "task_create called 2×, needed ≥ 3"),
    ({"calls": "code_query"}, True, "code_query called 1×"),
    ({"calls": "task_create", "at_most": 1}, False, "task_create called 2×, needed ≤ 1"),
    ({"calls": "task_create", "at_least": 2, "distinct": "project"}, True, "2 distinct project"),
    ({"calls": "feature_create", "at_least": 2, "distinct": "name"}, False, "1 distinct name, needed ≥ 2"),
    ({"calls": "task_create", "where": {"project": "web"}}, True, "task_create called 1×"),
    ({"calls": "task_create", "where": {"project": "ios"}}, False, "task_create called 0×, needed ≥ 1"),
    ({"before": ["code_query", "task_create"]}, True, "code_query before task_create"),
    ({"before": ["board_start", "task_create"]}, False, "board_start came after task_create"),
    ({"before": ["design_doc", "task_create"]}, False, "design_doc never called"),
    ({"before": ["code_query", "task_complete"]}, True, "task_complete never called"),
    ({"never": "task_complete"}, True, "task_complete never called"),
    ({"never": "board_start"}, False, "board_start called 1×, needed never"),
])
def test_each_check_pass_and_fail_with_reasons(check, ok, why):
    [r] = skill_tests.evaluate(scenario([check]), CALLS)
    assert r["ok"] is ok and why in r["why"], r
