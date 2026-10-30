"""The session queue and `aos lessons capture` (session learning, plan Task 2)."""

import json
import threading
import time
from pathlib import Path

import pytest
import yaml

from aos import sessions
from aos.cli import main
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from test_sessions_rules import result, say, use, user, write


@pytest.fixture
def env(configured, data, home, project, tmp_path):
    save_user_config({**load_user_config(), "projects": {"demo": {"path": str(project), "targets": ["claude"]}}})
    AOS(configured).register_project("demo")
    launched = []
    return configured, data, home, project, tmp_path, launched


def corrected(tmp_path, name="t.jsonl", extra=()):
    return write(tmp_path, [user("add a module"), say("I created MyModule.py"),
                            user("no, use snake_case for module names"), *extra], name)


def cap(env, transcript, session="s1", project_dir=None, settings=None):
    configured, data, home, project, tmp_path, launched = env
    aos = AOS(configured)
    s = settings or aos.settings
    return sessions.capture(home, data, s, "claude", transcript, session, project_dir or project,
                            launch=launched.append)


def test_capture_queues_a_session_with_corrections_and_launches_the_job(env):
    configured, data, home, project, tmp_path, launched = env
    status = cap(env, corrected(tmp_path))
    [entry] = sessions.entries(data)
    assert status.startswith("queued") and launched == [entry["id"]]
    assert (entry["tool"], entry["session"], entry["project"], entry["status"], entry["tries"]) == \
        ("claude", "s1", "demo", "pending", 0)
    assert [h["kind"] for h in entry["hits"]] == ["correction"]


def test_capture_ignores_a_session_without_corrections(env):
    configured, data, home, project, tmp_path, launched = env
    t = write(tmp_path, [user("add a module"), say("Done."), user("thanks")])
    assert cap(env, t) == "no corrections" and sessions.entries(data) == [] and launched == []


def test_capture_respects_settings_and_exclusions(env):
    configured, data, home, project, tmp_path, launched = env
    s = AOS(configured).settings
    off = {**s, "learning": {**s["learning"], "sessions": False}}
    assert cap(env, corrected(tmp_path), settings=off) == "off"
    excl = {**s, "learning": {**s["learning"], "sessions_exclude": ["demo"]}}
    assert cap(env, corrected(tmp_path), settings=excl) == "excluded"
    assert sessions.entries(data) == []


def test_automated_sessions_are_ignored(env):
    configured, data, home, project, tmp_path, launched = env
    for d in (home / "worktrees" / "f" / "demo", data / "runs" / "3-1"):
        d.mkdir(parents=True)
        assert cap(env, corrected(tmp_path), project_dir=d) == "automated session"
    assert sessions.entries(data) == []


def test_unlinked_directory_is_ignored(env, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert cap(env, corrected(tmp_path), project_dir=other) == "not a linked project"


def test_same_session_is_queued_once_per_new_turns(env):
    configured, data, home, project, tmp_path, launched = env
    t = corrected(tmp_path)
    cap(env, t)
    assert cap(env, t) == "nothing new" and len(sessions.entries(data)) == 1
    t = corrected(tmp_path, extra=[say("Renamed it."), user("instead put it in utils/")])
    assert cap(env, t).startswith("queued")
    new = sessions.entries(data)[-1]
    assert [h["kind"] for h in new["hits"]] == ["correction"] and len(sessions.entries(data)) == 2


def test_queue_stores_no_transcript_text(env):
    configured, data, home, project, tmp_path, launched = env
    cap(env, corrected(tmp_path))
    raw = next(sessions.queue_dir(data).glob("*.json")).read_text()
    assert "snake_case" not in raw and "MyModule" not in raw


def test_capture_never_fails(env, capsys):
    configured, data, home, project, tmp_path, launched = env
    assert cap(env, tmp_path / "missing.jsonl") == "error"
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json\n\x00\x01")
    assert cap(env, bad) == "no corrections"
    assert main(["lessons", "capture", "--tool", "claude", "--transcript", str(tmp_path / "nope"),
                 "--project-dir", str(project)]) == 0
    assert (data / "logs" / "sessions.log").read_text()


def test_capture_reads_the_hook_stdin(env, monkeypatch, capsys):
    configured, data, home, project, tmp_path, launched = env
    t = corrected(tmp_path)
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": "abc", "transcript_path": str(t), "cwd": str(project), "hook_event_name": "SessionEnd"})))
    monkeypatch.setattr(sessions, "_launch", lambda entry_id: launched.append(entry_id))
    assert main(["lessons", "capture", "--tool", "claude"]) == 0
    [entry] = sessions.entries(data)
    assert entry["session"] == "abc" and launched == [entry["id"]]


def test_capture_is_fast_on_a_huge_transcript(env):
    configured, data, home, project, tmp_path, launched = env
    big = tmp_path / "big.jsonl"
    filler = json.dumps(user("filler " + "z" * 2000)) + "\n"
    with open(big, "w") as fh:
        for _ in range(20000):
            fh.write(filler)
        for e in (user("add a module"), say("Done."), user("no, use snake_case")):
            fh.write(json.dumps(e) + "\n")
    start = time.monotonic()
    assert cap(env, big).startswith("queued")
    assert time.monotonic() - start < 2.0


def test_claim_finish_retry_and_prune(env):
    configured, data, home, project, tmp_path, launched = env
    cap(env, corrected(tmp_path))
    [e] = sessions.entries(data)
    got = []
    threads = [threading.Thread(target=lambda: got.append(sessions.claim(data, e["id"]))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for g in got if g) == 1
    for n in (1, 2):
        sessions.finish(data, e["id"], ok=False, reason="offline")
        assert sessions.entries(data)[0]["status"] == "pending" and sessions.entries(data)[0]["tries"] == n
        sessions.claim(data, e["id"])
    sessions.finish(data, e["id"], ok=False, reason="offline")
    assert sessions.entries(data)[0]["status"] == "failed"
    assert sessions.prune(data, days=7) == 0
    assert sessions.prune(data, days=0) == 1 and sessions.entries(data) == []
