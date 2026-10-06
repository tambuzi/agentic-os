from pathlib import Path

from aos import skills
from aos.config import load_settings
from aos.context import build_context
from aos.core import AOS

ROOT = Path(__file__).resolve().parents[1]


def test_seed_skills_valid():
    names = [s["name"] for s in skills.list_skills(ROOT)]
    assert {"aos-memory", "aos-onboard-project", "aos-plan-feature"} <= set(names)
    for n in names:
        skills.validate(n, (ROOT / "skills" / n / "SKILL.md").read_text())


def test_seed_settings():
    assert load_settings(ROOT)["memory"]["org"]["mode"] == "inbox"


def test_seed_context_fits_budget(home):
    out = build_context(AOS(ROOT, slug="demo"))
    assert len(out) <= 16000 and "agenticOS memory protocol" in out


def test_plan_feature_skill_hands_off_to_workers():
    text = (ROOT / "skills/aos-plan-feature/SKILL.md").read_text()
    assert "board_start" in text and "board_status" in text
    assert "do not implement" in text.lower()


def test_plan_feature_skill_uses_code_graph_for_every_project():
    text = (ROOT / "skills/aos-plan-feature/SKILL.md").read_text()
    assert "code_query" in text and "repo_map" in text


def test_protocols_point_at_code_query():
    from aos.context import PROTOCOL
    from aos.workers.common import WORKER_PROTOCOL
    assert "code_query" in PROTOCOL and "code_query" in WORKER_PROTOCOL


def test_plan_feature_skill_offers_kiro_workflow():
    text = (ROOT / "skills/aos-plan-feature/SKILL.md").read_text()
    assert "feature_workflow" in text and "Workflows" in text
