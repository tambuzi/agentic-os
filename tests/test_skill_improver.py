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


def test_a_failing_improver_is_retried_at_most_three_times(env):
    aos, data, tmp_path, project = env
    (aos.repo / "aos.yaml").write_text(yaml.safe_dump({
        "learning": {"learner_timeout_min": 0.5},
        "workers": {"claude": {"transport": "acp", "acp_command": [sys.executable, "-c", "import sys; sys.exit(3)"]}}}))
    aos = AOS(aos.repo)
    results = [skill_health.improve_flagged(aos) for _ in range(4)]
    assert all(r and r[0].startswith("failed") for r in results[:3]), results
    assert results[3] == [] and not skill_health.eligible(aos, "plan-it")
    later = date.today() + timedelta(days=15)                                     # failures age out
    assert skill_health.eligible(aos, "plan-it", today=later)                     # uses still within 30 days


def test_no_change_starts_the_cooldown(env, monkeypatch):
    aos, data, tmp_path, project = env
    monkeypatch.setenv("FAKE_ACP_IMPROVE", "nothing")
    assert skill_health.improve_flagged(aos) == ["no change"]
    assert skill_health.improve_flagged(aos) == []                                # not again every session


def test_no_evidence_skips_the_run(env):
    import shutil
    aos, data, tmp_path, project = env
    shutil.rmtree(data / "sessions" / "queue")                                    # pruned after 7 days
    [r] = skill_health.improve_flagged(aos)
    assert r.startswith("no change") and "no corrections" in r
    assert not (tmp_path / "agent.events").exists()                               # no AI run
    assert skill_health.improve_flagged(aos) == []


def test_one_improver_per_skill_at_a_time(env):
    import fcntl
    aos, data, tmp_path, project = env
    lock = data / "skills" / "improve-plan-it.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)                                            # another job is running it
        assert skill_health.improve_flagged(aos) == []


def test_improver_excerpts_hold_no_tool_output(env):
    aos, data, tmp_path, project = env
    roles = {t["role"] for ex in skill_health.improver_input(aos, "plan-it")["corrections"] for t in ex["turns"]}
    assert roles <= {"user", "assistant"} and "user" in roles


def test_skill_writes_are_staged_during_skill_tests(env, monkeypatch):
    aos, data, tmp_path, project = env
    monkeypatch.setenv("AOS_SKILL_TEST", "1")
    r = aos.skill_write("create", "made-in-a-test", {"content": "---\nname: made-in-a-test\ndescription: x\n---\nx\n"})
    assert "staged" in r and not (aos.repo / "skills" / "made-in-a-test").exists()


def test_skill_test_sessions_are_not_captured(env, monkeypatch):
    aos, data, tmp_path, project = env
    monkeypatch.setenv("AOS_SKILL_TEST", "1")
    t = write(tmp_path, [user("x"), use("k", "Skill", skill="plan-it"), say("y"), user("no, wrong")], "st.jsonl")
    before = len(skill_health.read_uses(data))
    assert sessions.capture(aos.home, data, aos.settings, "claude", t, "st", project, launch=lambda e: None) \
        == "skill test session"
    assert len(skill_health.read_uses(data)) == before
