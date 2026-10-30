"""The background session learner, its commands and doctor (session learning, plan Task 4)."""

import json
import sys
import threading
from datetime import date
from pathlib import Path

import pytest
import yaml

from aos import sessions
from aos.cli import main
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.lessons import LessonStore
from aos.session_learner import learn_entry, learn_last, learn_pending
from test_sessions_rules import say, user, write

ROOT = Path(__file__).resolve().parents[1]
FAKE_ACP = [sys.executable, str(Path(__file__).parent / "fake_acp_agent.py")]


@pytest.fixture
def env(configured, data, home, project, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT) + ":" + str(Path(__file__).parent))
    monkeypatch.setenv("FAKE_ACP_LOG", str(tmp_path / "agent.events"))
    save_user_config({**load_user_config(), "projects": {"demo": {"path": str(project), "targets": ["claude"]}}})
    AOS(configured).register_project("demo")
    (configured / "aos.yaml").write_text(yaml.safe_dump({
        "learning": {"learner_timeout_min": 0.5},
        "workers": {"claude": {"transport": "acp", "acp_command": FAKE_ACP}}}))
    aos = AOS(configured)
    t = write(tmp_path, [user("add a module"), say("I created MyModule.py"), user("no, use snake_case module names")])
    sessions.capture(aos.home, data, aos.settings, "claude", t, "s1", project, launch=lambda i: None)
    [entry] = sessions.entries(data)
    return aos, entry, tmp_path, data


def test_learn_entry_creates_a_session_lesson(env):
    aos, entry, tmp_path, data = env
    assert learn_entry(aos, entry["id"]).startswith("done")
    [lesson] = LessonStore(data, "demo").list()[0]
    assert lesson.confidence == 0.4 and lesson.evidence[0]["kind"] == "session"
    assert sessions.entry(data, entry["id"])["status"] == "done"


def test_failure_leaves_the_entry_pending_with_a_reason_then_failed_after_three(env, monkeypatch):
    aos, entry, tmp_path, data = env
    monkeypatch.setenv("FAKE_ACP_LEARN", "crash")
    for n in (1, 2):
        learn_entry(aos, entry["id"])
        e = sessions.entry(data, entry["id"])
        assert e["status"] == "pending" and e["tries"] == n and e["reason"]
    learn_entry(aos, entry["id"])
    assert sessions.entry(data, entry["id"])["status"] == "failed"


def test_daily_budget_defers_entries(env):
    aos, entry, tmp_path, data = env
    costs = Path(data) / "sessions" / "costs.jsonl"
    costs.parent.mkdir(parents=True, exist_ok=True)
    costs.write_text(json.dumps({"entry": "x", "amount": 5.0, "unit": "USD", "at": date.today().isoformat()}) + "\n")
    assert "budget" in learn_entry(aos, entry["id"])
    e = sessions.entry(data, entry["id"])
    assert e["status"] == "pending" and e["tries"] == 0          # deferring doesn't spend a try


def test_cost_is_recorded(env, monkeypatch):
    aos, entry, tmp_path, data = env
    monkeypatch.setenv("FAKE_ACP_COST", "0.02")
    learn_entry(aos, entry["id"])
    rows = [json.loads(l) for l in (Path(data) / "sessions" / "costs.jsonl").read_text().splitlines()]
    assert rows[-1]["entry"] == entry["id"] and rows[-1]["amount"] == pytest.approx(0.02)


def test_two_jobs_one_entry(env):
    aos, entry, tmp_path, data = env
    out = []
    threads = [threading.Thread(target=lambda: out.append(learn_entry(aos, entry["id"]))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(o.split(":")[0] for o in out) == ["done", "skipped"]


def test_learn_pending_processes_the_queue(env):
    aos, entry, tmp_path, data = env
    assert [r.split(":")[0] for r in learn_pending(aos)] == ["done"]
    assert learn_pending(aos) == []


def test_learn_last_queues_the_newest_transcript(env, monkeypatch, project):
    aos, entry, tmp_path, data = env
    fake_home = tmp_path / "fakehome"
    tdir = fake_home / ".claude" / "projects" / sessions.claude_project_folder(project)
    tdir.mkdir(parents=True)
    write(tdir, [user("explain the module layout"), say("It is flat.")], "older.jsonl")
    newest = write(tdir, [user("we keep modules flat"), say("Noted.")], "newest.jsonl")
    monkeypatch.setenv("HOME", str(fake_home))
    status = learn_last(aos, project)
    assert status.startswith("done")
    last = [e for e in sessions.entries(data) if e.get("all_turns")][-1]
    assert last["transcript"] == str(newest)


def test_cli_sessions_and_doctor_line(env, capsys):
    aos, entry, tmp_path, data = env
    assert main(["lessons", "sessions"]) == 0
    out = capsys.readouterr().out
    assert entry["id"] in out and "pending" in out
    assert main(["lessons", "learn-session", entry["id"]]) == 0
    assert "done" in capsys.readouterr().out
    assert main(["doctor"]) == 0
    assert "session learning: 0 pending, 0 failed" in capsys.readouterr().out
