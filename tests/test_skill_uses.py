"""Recording skill uses from finished sessions (skill health, plan Task 1)."""

import json
import time

import pytest

from aos import sessions, skill_health
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.sessions import find_corrections, read_claude_transcript
from test_sessions_rules import result, say, use, user, write


def skill(tid, name):
    return use(tid, "Skill", skill=name)


def uses_of(tmp_path, entries):
    turns = read_claude_transcript(write(tmp_path, entries))
    return [(u["skill"], u["outcome"]) for u in skill_health.skill_uses(turns, find_corrections(turns))]


def test_skill_use_ok_and_corrected(tmp_path):
    ok = [user("deliver a feature"), skill("s1", "aos-feature"), result("s1", "loaded"), say("Here's the plan."),
          user("great, go ahead")]
    assert uses_of(tmp_path, ok) == [("aos-feature", "ok")]
    bad = [user("deliver a feature"), skill("s1", "aos-feature"), result("s1", "loaded"), say("I'll do it myself."),
           user("no, use separate workers per project")]
    assert uses_of(tmp_path, bad) == [("aos-feature", "corrected")]


def test_correction_after_the_next_skill_use_counts_for_the_next(tmp_path):
    entries = [user("plan"), skill("s1", "aos-feature"), result("s1", "ok"), say("Planned."),
               user("now save a memory"), skill("s2", "aos-memory"), result("s2", "ok"), say("Saved to org."),
               user("no, that belongs in project memory")]
    assert uses_of(tmp_path, entries) == [("aos-feature", "ok"), ("aos-memory", "corrected")]


def test_window_is_15_turns(tmp_path):
    filler = [x for i in range(10) for x in (say(f"step {i}"), user(f"ok {i}"))]
    entries = [user("plan"), skill("s1", "aos-feature"), result("s1", "ok"), *filler, say("Done."), user("no, wrong")]
    assert uses_of(tmp_path, entries) == [("aos-feature", "ok")]


@pytest.fixture
def env(configured, data, project, tmp_path):
    save_user_config({**load_user_config(), "projects": {"demo": {"path": str(project), "targets": ["claude"]}}})
    AOS(configured).register_project("demo")
    return AOS(configured), data, project, tmp_path


def capture(env, t, settings=None, session="s1"):
    aos, data, project, tmp_path = env
    return sessions.capture(aos.home, data, settings or aos.settings, "claude", t, session, project, launch=lambda i: None)


def plan_session(tmp_path, name="t.jsonl", extra=()):
    return write(tmp_path, [user("deliver a feature"), skill("s1", "aos-feature"), result("s1", "ok"),
                            say("Plan ready."), user("approved"), *extra], name)


def test_uses_are_recorded_once_per_session(env):
    aos, data, project, tmp_path = env
    t = plan_session(tmp_path)
    capture(env, t)
    capture(env, t)
    rows = skill_health.read_uses(data)
    assert [(r["skill"], r["outcome"], r["project"], r["session"]) for r in rows] == [("aos-feature", "ok", "demo", "s1")]


def test_sessions_without_corrections_are_still_recorded(env):
    aos, data, project, tmp_path = env
    assert capture(env, plan_session(tmp_path)) == "no corrections"
    assert len(skill_health.read_uses(data)) == 1


def test_no_records_when_health_or_learning_is_off(env):
    aos, data, project, tmp_path = env
    s = aos.settings
    capture(env, plan_session(tmp_path), settings={**s, "skills": {**s["skills"], "health": False}})
    capture(env, plan_session(tmp_path), settings={**s, "learning": {**s["learning"], "enabled": False}})
    assert skill_health.read_uses(data) == []


def test_records_hold_no_text(env):
    aos, data, project, tmp_path = env
    capture(env, plan_session(tmp_path))
    raw = (data / "skills" / "runs.jsonl").read_text()
    assert "Plan ready" not in raw and "deliver a feature" not in raw


def test_skill_use_recording_is_bounded(env):
    aos, data, project, tmp_path = env
    entries = [user("go")]
    for i in range(5000):
        entries += [skill(f"s{i}", f"skill-{i % 7}"), result(f"s{i}", "ok")]
    t = write(tmp_path, entries)
    start = time.monotonic()
    capture(env, t)
    assert time.monotonic() - start < 2.0
    assert len(skill_health.read_uses(data)) <= skill_health.MAX_USES
