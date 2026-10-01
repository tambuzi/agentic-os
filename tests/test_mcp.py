import asyncio

from aos.core import AOS
from aos.mcp_server import Tools, build_server

GOOD = "---\nname: deploy-app\ndescription: Deploy the app safely.\n---\n\nSteps.\n"


def tools(repo, slug="shop"):
    return Tools(AOS(repo, slug=slug))


def test_memory_tools_roundtrip(repo, home):
    t = tools(repo)
    assert t.memory_add("project", "Uses Stripe.")["entries"] == ["Uses Stripe."]
    assert t.memory_read("project")["used"] > 0
    assert t.memory_replace("project", "Stripe", "Uses Stripe for payments.")["entries"] == ["Uses Stripe for payments."]
    assert t.memory_remove("project", "payments")["entries"] == []
    assert "staged" in t.memory_add("org", "FY starts April.", reason="told by user")


def test_errors_are_returned_not_raised(repo, home):
    t = tools(repo)
    r = t.memory_replace("project", "nope", "x")
    assert "error" in r and "hint" in r
    assert "error" in t.memory_read("team")
    assert "error" in t.knowledge_read("../x.md")
    assert "error" in t.skill_view("missing")
    assert "error" in t.project_context("missing")


def test_skill_manage_triggers_sync(repo, home):
    calls = []
    t = Tools(AOS(repo, slug="shop"), on_skills_changed=lambda: calls.append(1))
    assert t.skill_manage("create", "deploy-app", content=GOOD)["applied"]
    assert calls == [1]
    assert [s["name"] for s in t.skill_list()["skills"]] == ["deploy-app"]
    assert "error" in t.skill_manage("explode", "deploy-app")


def test_sync_failure_does_not_fail_tool(repo, home):
    def boom():
        raise RuntimeError("disk full")

    t = Tools(AOS(repo, slug="shop"), on_skills_changed=boom)
    r = t.skill_manage("create", "deploy-app", content=GOOD)
    assert r["applied"] and "disk full" in r["sync_warning"]


def test_knowledge_and_projects(repo, home):
    (repo / "knowledge/a.md").write_text("# Pricing\nPlans are monthly.\n")
    t = tools(repo)
    assert t.knowledge_search("monthly")["results"][0]["path"] == "knowledge/a.md"
    assert t.knowledge_read("knowledge/a.md")["content"].startswith("# Pricing")
    AOS(repo).register_project("shop", "Shop API")
    assert t.project_list()["projects"] == [{"slug": "shop", "description": "Shop API"}]


def test_server_registers_all_tools(repo, home):
    mcp = build_server(tools(repo))
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == {"memory_read", "memory_add", "memory_replace", "memory_remove",
                     "knowledge_search", "knowledge_read", "skill_list", "skill_view",
                     "skill_manage", "project_list", "project_context"}
