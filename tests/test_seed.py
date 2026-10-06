from pathlib import Path

from aos import skills
from aos.config import load_settings
from aos.context import build_context
from aos.core import AOS

ROOT = Path(__file__).resolve().parents[1]


def test_seed_skills_valid():
    names = [s["name"] for s in skills.list_skills(ROOT)]
    assert {"aos-memory", "aos-onboard-project", "aos-feature"} <= set(names)
    assert "aos-plan-feature" not in names  # replaced by the aos-feature master skill
    for n in names:
        skills.validate(n, (ROOT / "skills" / n / "SKILL.md").read_text())


def test_seed_settings():
    assert load_settings(ROOT)["memory"]["org"]["mode"] == "inbox"


def test_seed_context_fits_budget(home):
    out = build_context(AOS(ROOT, slug="demo"))
    assert len(out) <= 16000 and "agenticOS memory protocol" in out


def test_plan_feature_skill_hands_off_to_workers():
    text = (ROOT / "skills/aos-feature/SKILL.md").read_text()
    assert "board_start" in text and "board_wait" in text
    assert "never implement" in text.lower()


def test_plan_feature_skill_uses_code_graph_for_every_project():
    text = (ROOT / "skills/aos-feature/SKILL.md").read_text()
    assert "code_query" in text and "repo_map" in text


def test_protocols_point_at_code_query():
    from aos.context import PROTOCOL
    from aos.workers.common import WORKER_PROTOCOL
    assert "code_query" in PROTOCOL and "code_query" in WORKER_PROTOCOL


def test_plan_feature_skill_offers_kiro_workflow():
    text = (ROOT / "skills/aos-feature/SKILL.md").read_text()
    assert "feature_workflow" in text and "Workflows" in text


def test_master_skill_covers_the_whole_flow():
    text = (ROOT / "skills/aos-feature/SKILL.md").read_text()
    for tool in ("feature_create", "task_create", "board_start", "board_wait", "task_unblock",
                 "proposal_decide", "task_retry", "feature_workflow", "code_query"):
        assert tool in text, tool
    for reason in ("attention", "stalled", "timeout", "waiting_on_human", "finished"):
        assert reason in text, reason
