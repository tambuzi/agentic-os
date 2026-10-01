import pytest

from aos import inbox
from aos.core import AOS
from aos.errors import AosError

GOOD = "---\nname: deploy-app\ndescription: Deploy the app safely.\n---\n\nSteps.\n"


def test_project_memory_direct(repo, home, data):
    res = AOS(repo, slug="shop").memory_write("project", "add", {"text": "Shop uses Stripe."})
    assert res["entries"] == ["Shop uses Stripe."]
    assert (data / "memory/projects/shop.md").exists()
    assert not (repo / "memory").exists()


def test_org_memory_staged_then_applied(repo, home, data):
    aos = AOS(repo, slug="shop")
    res = aos.memory_write("org", "add", {"text": "Fiscal year starts in April."},
                           source="project:shop", reason="user said so")
    assert "staged" in res
    assert aos.memory("org").entries() == []
    [p] = inbox.list_proposals(data)
    assert not (repo / "inbox").exists()
    assert p["args"]["scope"] == "org" and p["reason"] == "user said so"
    aos.apply_proposal(res["staged"])
    assert aos.memory("org").entries() == ["Fiscal year starts in April."]
    assert inbox.list_proposals(data) == []
    assert (data / "memory/org.md").exists() and not (repo / "memory").exists()


def test_reject_proposal(repo, home, data):
    aos = AOS(repo)
    pid = aos.memory_write("org", "add", {"text": "x"})["staged"]
    aos.reject_proposal(pid)
    assert inbox.list_proposals(data) == []
    with pytest.raises(AosError):
        aos.apply_proposal(pid)
    with pytest.raises(AosError):
        aos.apply_proposal("../../SOUL")


def test_scope_errors(repo, home):
    with pytest.raises(AosError):
        AOS(repo).memory_read("project")
    with pytest.raises(AosError):
        AOS(repo).memory_read("team")


def test_user_memory_lives_in_home(repo, home):
    AOS(repo).memory_write("user", "add", {"text": "Prefers Spanish."})
    assert (home / "user.md").read_text().startswith("Prefers Spanish.")


def test_missing_args(repo, home):
    with pytest.raises(AosError):
        AOS(repo).memory_write("user", "replace", {"old": "a"})
    with pytest.raises(AosError):
        AOS(repo).memory_write("user", "explode", {})


def test_skill_inbox_mode(repo, home):
    (repo / "aos.yaml").write_text("skills:\n  mode: inbox\n")
    aos = AOS(repo, slug="shop")
    res = aos.skill_write("create", "deploy-app", {"content": GOOD})
    assert "staged" in res
    assert not (repo / "skills/deploy-app").exists()
    assert aos.apply_proposal(res["staged"])["applied"]
    assert (repo / "skills/deploy-app/SKILL.md").exists()


def test_skill_direct_mode(repo, home):
    res = AOS(repo).skill_write("create", "deploy-app", {"content": GOOD})
    assert res == {"created": "deploy-app", "applied": True}


def test_projects_registry(repo, home, data):
    aos = AOS(repo, slug="shop")
    aos.register_project("shop", "Online shop API")
    aos.register_project("shop", "ignored")
    aos.memory_write("project", "add", {"text": "Uses Stripe."})
    assert aos.project_list() == [{"slug": "shop", "description": "Online shop API"}]
    assert (data / "projects.yaml").exists() and not (repo / "projects.yaml").exists()
    ctx = AOS(repo, slug="crm").project_context("shop")
    assert ctx["memory"] == ["Uses Stripe."]
    with pytest.raises(AosError):
        aos.project_context("nope")
