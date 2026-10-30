"""`aos link` installs the end-of-session hook; unlink removes it (session learning, plan Task 5)."""

import json

import pytest

from aos.link import link, unlink


def load(p):
    return json.loads(p.read_text())


@pytest.fixture
def env(configured, project, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: "/bin/aos" if n == "aos" else None)
    return project


def end_hooks(p):
    return load(p / ".claude/settings.local.json")["hooks"].get("SessionEnd", [])


def test_link_installs_the_session_end_hook(env):
    link(env, slug="shop")
    [group] = end_hooks(env)
    assert group["hooks"] == [{"type": "command", "command": '"/bin/aos" lessons capture --tool claude'}]


def test_relink_does_not_duplicate(env):
    link(env, slug="shop")
    link(env, slug="shop")
    assert len(end_hooks(env)) == 1


def test_unlink_removes_it(env):
    link(env, slug="shop")
    unlink(env)
    p = env / ".claude/settings.local.json"
    assert not p.exists() or "SessionEnd" not in (load(p).get("hooks") or {})
