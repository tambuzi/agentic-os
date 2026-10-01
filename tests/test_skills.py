import pytest

from aos import skills
from aos.errors import AosError

GOOD = "---\nname: deploy-app\ndescription: Deploy the app safely.\n---\n\n# Deploy\n\n1. Run tests.\n"


def test_create_adds_provenance(repo):
    skills.create(repo, "deploy-app", GOOD)
    meta, body = skills.split_frontmatter((repo / "skills/deploy-app/SKILL.md").read_text())
    assert meta["metadata"]["aos"]["created_by"] == "agent"
    assert "1. Run tests." in body


def test_create_validates(repo):
    with pytest.raises(AosError):
        skills.create(repo, "Bad Name", GOOD)
    with pytest.raises(AosError):
        skills.create(repo, "other", GOOD)
    with pytest.raises(AosError):
        skills.create(repo, "deploy-app", "no frontmatter")
    skills.create(repo, "deploy-app", GOOD)
    with pytest.raises(AosError):
        skills.create(repo, "deploy-app", GOOD)


def test_list_and_project_filter(repo, make_skill):
    make_skill("general")
    make_skill("shop-only", projects=["shop"])
    assert [s["name"] for s in skills.list_skills(repo)] == ["general", "shop-only"]
    assert skills.skills_for(repo, "shop") == ["general", "shop-only"]
    assert skills.skills_for(repo, "crm") == ["general"]


def test_patch_unique(repo):
    skills.create(repo, "deploy-app", GOOD)
    skills.patch(repo, "deploy-app", "1. Run tests.", "1. Run tests.\n2. Tag release.")
    assert "Tag release" in skills.view(repo, "deploy-app")["content"]
    with pytest.raises(AosError):
        skills.patch(repo, "deploy-app", "nothing here", "x")
    with pytest.raises(AosError):
        skills.patch(repo, "deploy-app", "name: deploy-app", "name: renamed")
    assert "name: deploy-app" in skills.view(repo, "deploy-app")["content"]


def test_write_file_and_traversal(repo):
    skills.create(repo, "deploy-app", GOOD)
    skills.write_file(repo, "deploy-app", "scripts/run.sh", "echo hi\n")
    assert skills.view(repo, "deploy-app")["files"] == ["scripts/run.sh"]
    with pytest.raises(AosError):
        skills.write_file(repo, "deploy-app", "../../evil.md", "x")
    with pytest.raises(AosError):
        skills.write_file(repo, "deploy-app", "SKILL.md", "x")


def test_delete_archives(repo):
    skills.create(repo, "deploy-app", GOOD)
    skills.delete(repo, "deploy-app")
    assert not (repo / "skills/deploy-app").exists()
    assert (repo / "skills/.archive/deploy-app/SKILL.md").exists()
    assert skills.list_skills(repo) == []
    with pytest.raises(AosError):
        skills.view(repo, "deploy-app")
