"""Lessons confirmed in 2+ projects become one org-memory proposal in the inbox (spec §5.3)."""

import pytest

from aos import inbox, lessons
from aos.cli import main
from aos.core import AOS
from aos.lessons import LessonStore

EV = [{"feature": "f", "task": 1, "events": [1], "kind": "review_fail", "at": "2026-10-29"}]


@pytest.fixture
def aos(configured, data):
    a = AOS(configured)
    for p in ("api", "web"):
        a.register_project(p)
    return a


def linked(aos, rule="Ship a migration with every new table."):
    web = LessonStore(aos.data, "web")
    w = web.decide(web.new("when adding a table in web", "Write a migration for web tables.", "database", EV).id, True)
    api = LessonStore(aos.data, "api")
    a = api.new("when adding a table", "Add a migration in api.", "database", EV,
                same_as=[f"web/{w.id}"], general_rule=rule)
    return api, a, web, w


def test_one_proposal_per_group(aos):
    api, a, web, w = linked(aos)
    assert lessons.stage_promotions(aos) == []           # api lesson not confirmed yet
    api.decide(a.id, True)
    [pid] = lessons.stage_promotions(aos)
    assert lessons.stage_promotions(aos) == []           # pending: not again
    p = inbox.load(aos.data, pid)
    assert (p["kind"], p["op"], p["args"]["scope"], p["args"]["text"]) == \
        ("memory", "add", "org", "Ship a migration with every new table.")
    assert sorted(p["args"]["lessons"]) == sorted([f"api/{a.id}", f"web/{w.id}"])
    assert "confirmed in 2 projects" in p["reason"]
    assert api.get(a.id).promotion == pid and web.get(w.id).promotion == pid


def test_fallback_rule_drops_project_names(aos):
    api, a, web, w = linked(aos)
    # a group whose lessons carry no general_rule (e.g. linked from the other side, hand-edited)
    text = (api.dir / f"{a.id}.md").read_text().replace("general_rule: Ship a migration with every new table.",
                                                         "general_rule: null")
    (api.dir / f"{a.id}.md").write_text(text)
    api.decide(a.id, True)
    [pid] = lessons.stage_promotions(aos)
    rule = inbox.load(aos.data, pid)["args"]["text"]
    assert "api" not in rule.split() and "web" not in rule.split() and "migration" in rule


def test_apply_promotes(aos, capsys):
    api, a, web, w = linked(aos)
    api.decide(a.id, True)
    [pid] = lessons.stage_promotions(aos)
    assert main(["inbox", "apply", pid]) == 0
    assert "Ship a migration with every new table." in aos.memory("org").entries()
    assert api.get(a.id).promoted_to == "org" and api.get(a.id).promotion is None
    assert web.get(w.id).promoted_to == "org"
    assert lessons.stage_promotions(aos) == []


def test_reject_declines_for_good(aos, capsys):
    api, a, web, w = linked(aos)
    api.decide(a.id, True)
    [pid] = lessons.stage_promotions(aos)
    assert main(["inbox", "reject", pid]) == 0
    assert api.get(a.id).promotion is None and api.get(a.id).promoted_to is None
    assert lessons.stage_promotions(aos) == []
    assert aos.memory("org").entries() == []


def test_a_declined_group_stays_declined_when_a_project_joins(aos, capsys):
    api, a, web, w = linked(aos)
    api.decide(a.id, True)
    [pid] = lessons.stage_promotions(aos)
    assert main(["inbox", "reject", pid]) == 0
    billing = LessonStore(aos.data, "billing")
    b = billing.new("when adding a table", "Add a migration in billing.", "database", EV,
                    same_as=[f"web/{w.id}"], general_rule="Ship a migration with every new table.")
    billing.decide(b.id, True)
    assert len(lessons.groups(aos.data)[0]) == 3
    assert lessons.stage_promotions(aos) == []
