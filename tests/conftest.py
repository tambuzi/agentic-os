from pathlib import Path

import pytest
import yaml

from aos.config import save_user_config


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("AOS_HOME", str(h))
    monkeypatch.delenv("AOS_REPO", raising=False)
    return h


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "agenticOS"
    (r / "skills").mkdir(parents=True)
    (r / "SOUL.md").write_text("You are the team agent.\n")
    (r / "AGENTS.md").write_text("# Org rules\n- Use UTC.\n")
    return r


@pytest.fixture
def data(home) -> Path:
    """Local business data dir (default: $AOS_HOME/data)."""
    d = home / "data"
    (d / "memory" / "projects").mkdir(parents=True)
    (d / "knowledge").mkdir()
    return d


@pytest.fixture
def configured(home, repo) -> Path:
    save_user_config({"repo": str(repo), "projects": {}})
    return repo


@pytest.fixture
def project(tmp_path) -> Path:
    p = tmp_path / "shop-api"
    p.mkdir()
    return p


@pytest.fixture
def make_skill(repo):
    def _make(name, description="Does a thing.", body="Steps.\n", projects=None, files=None) -> Path:
        meta = {"name": name, "description": description}
        if projects:
            meta["metadata"] = {"aos": {"projects": projects}}
        d = repo / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n\n" + body)
        for rel, text in (files or {}).items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(text)
        return d

    return _make


@pytest.fixture
def git_repo(monkeypatch):
    """Factory: a git repo on branch main with one commit."""
    import subprocess
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)

    def _make(path: Path) -> Path:
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
        (path / "README.md").write_text("hi\n")
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
        return path

    return _make
