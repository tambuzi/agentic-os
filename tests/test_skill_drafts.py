"""`aos skills test --draft` and the shipped aos-feature tests (plan Task 6)."""

import sys
from pathlib import Path

import pytest
import yaml

from aos import skill_tests
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.errors import AosError

ROOT = Path(__file__).resolve().parents[1]
FAKE_ACP = [sys.executable, str(Path(__file__).parent / "fake_acp_agent.py")]


@pytest.fixture
def env(configured, data, tmp_path, make_skill, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT) + ":" + str(Path(__file__).parent))
    monkeypatch.setenv("FAKE_ACP_LOG", str(tmp_path / "agent.events"))
    make_skill("plan-it", body="Plan with the user.\nDo not create anything before approval.\n")
    (configured / "aos.yaml").write_text(yaml.safe_dump({
        "learning": {"learner_timeout_min": 0.5},
        "workers": {"claude": {"transport": "acp", "acp_command": FAKE_ACP}}}))
    return AOS(configured)


def test_draft_writes_a_valid_draft_file(env, monkeypatch):
    monkeypatch.setenv("FAKE_ACP_DRAFT", "valid")
    path = skill_tests.draft(env, "plan-it")
    assert path.parent == env.repo / "skills" / "plan-it" / "tests" and path.name.startswith("draft-")
    s = skill_tests.load(path)
    assert s.skill == "plan-it" and s.draft
    assert skill_tests.discover(env.repo, "plan-it")[0] == []           # drafts never run until renamed


def test_invalid_draft_is_refused(env, monkeypatch):
    monkeypatch.setenv("FAKE_ACP_DRAFT", "invalid")
    with pytest.raises(AosError, match="no valid test"):
        skill_tests.draft(env, "plan-it")
    assert not (env.repo / "skills" / "plan-it" / "tests").exists() or \
        not list((env.repo / "skills" / "plan-it" / "tests").glob("*.yaml"))


def test_write_test_tool_validates(env):
    from aos.mcp_server import SkillDraftTools
    dt = SkillDraftTools(env, "plan-it")
    bad = dt.write_test("scenario: x\nturns: [hi]\nchecks: [{sometimes: task_create}]\n")
    assert bad["ok"] is False and "sometimes" in bad["error"]
    good = dt.write_test("scenario: Asks First\nturns: [hi]\nchecks: [{never: task_create}]\n")
    assert good["ok"] and good["path"].endswith("draft-asks-first.yaml")
    assert dt.write_test("scenario: y\nturns: [hi]\nchecks: [{never: x}]\n")["ok"] is False   # one per run


def test_shipped_aos_feature_tests_validate():
    ok, errors = skill_tests.discover(ROOT, "aos-feature")
    assert errors == []
    assert sorted(s.name for s in ok) == ["asks-before-creating", "plan-two-projects"]
