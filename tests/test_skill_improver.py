"""The skill improver: fix proposals for skills that keep getting corrected (plan Task 3)."""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from aos import inbox, sessions, skill_health
from aos.cli import main
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from test_sessions_rules import result, say, use, user, write

ROOT = Path(__file__).resolve().parents[1]
FAKE_ACP = [sys.executable, str(Path(__file__).parent / "fake_acp_agent.py")]
SKILL_BODY = "Plan the feature with the user.\nRun one worker per project.\nReport at the end.\n"


@pytest.fixture
def env(configured, data, project, tmp_path, make_skill, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT) + ":" + str(Path(__file__).parent))
    monkeypatch.setenv("FAKE_ACP_LOG", str(tmp_path / "agent.events"))
    save_user_config({**load_user_config(), "projects": {"demo": {"path": str(project), "targets": ["claude"]}}})
    AOS(configured).register_project("demo")
    make_skill("plan-it", body=SKILL_BODY)
    (configured / "aos.yaml").write_text(yaml.safe_dump({
        "learning": {"learner_timeout_min": 0.5},
        "workers": {"claude": {"transport": "acp", "acp_command": FAKE_ACP}}}))
    aos = AOS(configured)
    # five real sessions using the skill; three corrected right after it
    for i in range(5):
        corrected = i < 3
        t = write(tmp_path, [user("deliver a feature"), use("k", "Skill", skill="plan-it"), result("k", "loaded"),
                             say("I'll write the code myself."),
                             user("no, run one worker per project" if corrected else "great, go ahead")], f"s{i}.jsonl")
        sessions.capture(aos.home, data, aos.settings, "claude", t, f"s{i}", project, launch=lambda e: None)
    return aos, data, tmp_path, project


def test_eligibility(env, make_skill):
    aos, data, tmp_path, project = env
    assert skill_health.eligible(aos, "plan-it")
    assert not skill_health.eligible(aos, "superpowers:brainstorming")            # not a repo skill
    pid = skill_health.propose(aos, "plan-it", "Report at the end.", "Report at the end, with the worktree paths.", "r")
    assert not skill_health.eligible(aos, "plan-it")                                # open proposal
    aos.reject_proposal(pid)
    assert not skill_health.eligible(aos, "plan-it")                                # cooldown after a reject


def test_improver_input_is_quoted_and_scrubbed(env):
    aos, data, tmp_path, project = env
    data_in = skill_health.improver_input(aos, "plan-it")
    assert data_in["text"].count("Run one worker per project.") == 1
    assert len(data_in["corrections"]) == 3
    assert all(t["text"].startswith("<<<quoted\n") for ex in data_in["corrections"] for t in ex["turns"])


@pytest.mark.parametrize("old,new,reason,match", [
    ("not in the skill", "x", "r", "exactly once"),
    ("Report at the end.", "x" * 2001, "r", "2000"),
    ("Report at the end.", "token: sk-abcdefghijklmnopqrstuv", "r", "secret"),
    ("Report at the end.", "ok", "r" * 501, "500"),
])
def test_propose_validation(env, old, new, reason, match):
    aos, data, tmp_path, project = env
    from aos.lessons import LessonError
    with pytest.raises(Exception, match=match):
        skill_health.propose(aos, "plan-it", old, new, reason)


def test_proposal_applies_through_the_skill_writer(env, capsys):
    aos, data, tmp_path, project = env
    pid = skill_health.propose(aos, "plan-it", "Run one worker per project.",
                               "Run one worker per project; never write the code yourself.", "3 of 5 corrected")
    p = inbox.load(data, pid)
    assert (p["kind"], p["op"], p["source"]) == ("skill", "patch", "skill-health") and "3 of 5" in p["reason"]
    assert main(["inbox", "apply", pid]) == 0
    text = (aos.repo / "skills" / "plan-it" / "SKILL.md").read_text()
    assert "never write the code yourself" in text


def test_stale_proposal_fails_cleanly(env, capsys):
    aos, data, tmp_path, project = env
    pid = skill_health.propose(aos, "plan-it", "Report at the end.", "Report at the end, briefly.", "r")
    md = aos.repo / "skills" / "plan-it" / "SKILL.md"
    md.write_text(md.read_text().replace("Report at the end.", "Summarise at the end."))
    before = md.read_text()
    assert main(["inbox", "apply", pid]) != 0
    assert md.read_text() == before


def test_reject_starts_the_cooldown(env):
    aos, data, tmp_path, project = env
    pid = skill_health.propose(aos, "plan-it", "Report at the end.", "Report at the end, briefly.", "r")
    aos.reject_proposal(pid)
    rows = [json.loads(l) for l in (data / "skills" / "proposals.jsonl").read_text().splitlines()]
    assert rows[-1]["status"] == "rejected"


def test_improve_flagged_with_the_fake_agent(env, monkeypatch):
    aos, data, tmp_path, project = env
    monkeypatch.setenv("FAKE_ACP_IMPROVE", "propose")
    [r] = skill_health.improve_flagged(aos)
    assert r.startswith("proposed")
    [p] = [p for p in inbox.list_proposals(data) if p.get("source") == "skill-health"]
    assert p["args"]["name"] == "plan-it"


def test_improver_session_is_ignored_by_capture(env):
    aos, data, tmp_path, project = env
    run_dir = data / "runs" / "improve-plan-it-1"
    run_dir.mkdir(parents=True)
    t = write(tmp_path, [user("x"), say("y"), user("no, wrong")], "improver.jsonl")
    assert sessions.capture(aos.home, data, aos.settings, "claude", t, "imp", run_dir, launch=lambda e: None) \
        == "automated session"
