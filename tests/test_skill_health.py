"""Skill health stats, `aos skills health` and the doctor line (plan Task 2)."""

import json
from datetime import date, timedelta

import pytest

from aos import skill_health
from aos.cli import main
from aos.core import AOS

TODAY = date(2026, 11, 3)


def rows(data, items):
    p = data / "skills" / "runs.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        for i, (skill, outcome, days_ago) in enumerate(items):
            at = (TODAY - timedelta(days=days_ago)).isoformat() + "T10:00:00+00:00"
            fh.write(json.dumps({"skill": skill, "tool": "claude", "project": "demo", "session": f"s{i}",
                                 "key": f"k{i}", "outcome": outcome, "at": at}) + "\n")


def by_skill(configured, data):
    aos = AOS(configured)
    return {h["skill"]: h for h in skill_health.health(data, aos.settings, aos.repo, today=TODAY)}


def test_stats_and_rates(configured, data):
    rows(data, [("aos-feature", "ok", 1), ("aos-feature", "corrected", 2), ("aos-feature", "ok", 20),
                ("aos-feature", "corrected", 40)])
    h = by_skill(configured, data)["aos-feature"]
    assert (h["uses_7"], h["uses_30"]) == (2, 3)
    assert h["rate_7"] == pytest.approx(0.5) and h["rate_30"] == pytest.approx(1 / 3)
    assert h["last_used"] == (TODAY - timedelta(days=1)).isoformat()


def test_trend_with_margin(configured, data):
    rows(data, [("a", "corrected", 20), ("a", "corrected", 25), ("a", "ok", 2), ("a", "ok", 3),     # better lately
                ("b", "ok", 20), ("b", "ok", 25), ("b", "corrected", 2), ("b", "corrected", 3),     # worse lately
                ("c", "ok", 20), ("c", "corrected", 25), ("c", "ok", 2), ("c", "corrected", 3)])    # steady
    h = by_skill(configured, data)
    assert (h["a"]["trend"], h["b"]["trend"], h["c"]["trend"]) == ("↑", "↓", "=")


def test_flag_thresholds(configured, data):
    rows(data, [("two", "corrected", 1), ("two", "corrected", 2)]
         + [("three-of-five", o, 3) for o in ("corrected",) * 3 + ("ok",) * 2]
         + [("three-of-ten", o, 3) for o in ("corrected",) * 3 + ("ok",) * 7])
    h = by_skill(configured, data)
    assert not h["two"]["flagged"] and h["three-of-five"]["flagged"] and not h["three-of-ten"]["flagged"]
    assert skill_health.flagged(data, AOS(configured).settings, AOS(configured).repo, today=TODAY) == ["three-of-five"]


def test_repo_skill_vs_plugin(configured, data, make_skill):
    make_skill("deploy-app")
    rows(data, [("deploy-app", "ok", 1), ("superpowers:brainstorming", "ok", 1)])
    h = by_skill(configured, data)
    assert h["deploy-app"]["repo_skill"] and not h["superpowers:brainstorming"]["repo_skill"]


def test_tests_pass_rate_from_results(configured, data):
    rows(data, [("aos-feature", "ok", 1)])
    p = data / "skills" / "tests" / "results.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"skill": "aos-feature", "passed": 1, "total": 2, "at": "2026-11-01"}) + "\n"
                 + json.dumps({"skill": "aos-feature", "passed": 2, "total": 2, "at": "2026-11-02"}) + "\n")
    assert by_skill(configured, data)["aos-feature"]["tests"] == 1.0


def test_cli_and_doctor(configured, data, capsys):
    assert main(["skills", "health"]) == 0
    assert "no skill uses recorded yet" in capsys.readouterr().out
    today = date.today()
    p = data / "skills" / "runs.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps({"skill": "aos-feature", "outcome": "corrected" if i < 3 else "ok",
                                     "session": f"s{i}", "key": f"k{i}", "at": today.isoformat() + "T10:00:00+00:00"})
                         + "\n" for i in range(5)))
    assert main(["skills", "health"]) == 0
    out = capsys.readouterr().out
    assert "aos-feature" in out and "60%" in out and "flagged" in out
    main(["doctor"])
    assert "skills: 1 used, 1 flagged: aos-feature" in capsys.readouterr().out
