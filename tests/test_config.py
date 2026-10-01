import pytest

from aos.config import (
    check_slug, data_root, default_slug, graphskill_cmd, load_settings, repo_root,
    save_user_config, slug_for_path,
)
from aos.errors import AosError


def test_settings_defaults_and_override(repo):
    s = load_settings(repo)
    assert s["memory"]["org"]["mode"] == "inbox"
    assert s["memory"]["project"]["cap"] == 2200
    (repo / "aos.yaml").write_text("memory:\n  org:\n    mode: direct\n")
    s = load_settings(repo)
    assert s["memory"]["org"]["mode"] == "direct"
    assert s["memory"]["org"]["cap"] == 4000


def test_repo_root_requires_config(home):
    with pytest.raises(AosError) as e:
        repo_root()
    assert "aos init" in e.value.hint


def test_repo_root_from_config(configured):
    assert repo_root() == configured.resolve()


def test_repo_root_rejects_non_repo(home, tmp_path):
    save_user_config({"repo": str(tmp_path), "projects": {}})
    with pytest.raises(AosError):
        repo_root()


def test_slug_for_path_matches_subdirs(home, project):
    save_user_config({"repo": "x", "projects": {"shop": {"path": str(project), "targets": ["claude"]}}})
    (project / "src").mkdir()
    assert slug_for_path(project / "src") == "shop"
    assert slug_for_path(project.parent) is None


def test_default_slug(tmp_path):
    assert default_slug(tmp_path / "My Shop_API") == "my-shop-api"


def test_check_slug():
    check_slug("ok-1")
    with pytest.raises(AosError):
        check_slug("../x")


def test_graphskill_cmd(home, monkeypatch):
    monkeypatch.setattr("aos.config.shutil.which", lambda n: None)
    assert graphskill_cmd() is None
    save_user_config({"projects": {}, "graphskill_cmd": ["/x/python", "-m", "graphskill"]})
    assert graphskill_cmd() == ["/x/python", "-m", "graphskill"]


def test_data_root_default_and_override(home, tmp_path):
    assert data_root() == home / "data"
    save_user_config({"projects": {}, "data_dir": str(tmp_path / "biz")})
    assert data_root() == (tmp_path / "biz").resolve()
