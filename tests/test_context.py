from aos.context import build_context
from aos.core import AOS


def test_context_order_and_content(repo, home):
    aos = AOS(repo, slug="shop")
    aos.register_project("shop", "Shop API")
    aos.register_project("crm", "CRM backend")
    aos.memory_write("project", "add", {"text": "Shop uses Stripe."})
    aos.memory_write("user", "add", {"text": "Prefers Spanish."})
    out = build_context(aos)
    order = ["You are the team agent.", "Use UTC.", "agenticOS memory protocol", "Org memory",
             "(empty)", "Shop uses Stripe.", "Prefers Spanish.", "- crm: CRM backend"]
    pos = [out.index(s) for s in order]
    assert pos == sorted(pos)
    assert "- shop" not in out


def test_context_without_project(repo, home):
    out = build_context(AOS(repo))
    assert "Project memory" not in out and "Org memory" in out


def test_context_budget_trims_project_index(repo, home):
    (repo / "aos.yaml").write_text("context:\n  max_chars: 3000\n")
    aos = AOS(repo, slug="shop")
    for i in range(200):
        aos.register_project(f"p{i}", "x" * 40)
    out = build_context(aos)
    assert len(out) <= 3000
    assert "- p0:" in out
    assert "- p99:" not in out


def test_context_hard_truncates(repo, home):
    (repo / "aos.yaml").write_text("context:\n  max_chars: 500\n")
    (repo / "SOUL.md").write_text("S" * 2000)
    out = build_context(AOS(repo, slug="shop"))
    assert len(out) <= 500
    assert out.rstrip().endswith("[aos: context truncated]")
