import pytest

from aos.errors import AosError
from aos.knowledge import KnowledgeIndex


def idx(repo, home, data):
    return KnowledgeIndex(repo, home / "aos.db", data)


def test_search_finds_knowledge_memory_and_skills(repo, home, data, make_skill):
    (data / "knowledge/billing.md").write_text("# Billing\n\nInvoices are issued on the 1st via Stripe.\n")
    (data / "memory/org.md").write_text("Fiscal year starts in April.\n")
    make_skill("refunds", description="Process a customer refund in Stripe.")
    paths = {r["path"] for r in idx(repo, home, data).search("stripe")}
    assert paths == {"knowledge/billing.md", "skills/refunds/SKILL.md"}
    [r] = idx(repo, home, data).search("fiscal april")
    assert r["path"] == "memory/org.md"


def test_title_and_snippet(repo, home, data):
    (data / "knowledge/billing.md").write_text("# Billing\n\nInvoices via Stripe.\n")
    [r] = idx(repo, home, data).search("invoices")
    assert r["title"] == "Billing"
    assert "[Invoices]" in r["snippet"]


def test_reindexes_on_change(repo, home, data):
    k = idx(repo, home, data)
    assert k.search("kafka") == []
    (data / "knowledge/events.md").write_text("We use Kafka.\n")
    assert len(k.search("kafka")) == 1
    (data / "knowledge/events.md").unlink()
    assert k.search("kafka") == []


def test_query_with_operators_and_punctuation(repo, home, data):
    (data / "knowledge/a.md").write_text("multi-tenant AND billing\n")
    k = idx(repo, home, data)
    for q in ["multi-tenant", "AND", '"billing', "NEAR(x", "*", ""]:
        k.search(q)
    assert k.search("multi-tenant")


def test_read_and_traversal(repo, home, data):
    (data / "knowledge/a.md").write_text("hello\n")
    k = idx(repo, home, data)
    assert k.read("knowledge/a.md") == {"path": "knowledge/a.md", "content": "hello\n"}
    assert k.read("SOUL.md")["content"].startswith("You are")  # code-side docs still come from the repo
    for bad in ["../secret.md", "knowledge/missing.md", "SOUL.md/../../x.md", "aos.yaml"]:
        with pytest.raises(AosError):
            k.read(bad)


def test_data_never_read_from_repo(repo, home, data):
    (repo / "knowledge").mkdir()
    (repo / "knowledge/leak.md").write_text("secret pricing\n")
    k = idx(repo, home, data)
    assert k.search("secret") == []
    with pytest.raises(AosError):
        k.read("knowledge/leak.md")
