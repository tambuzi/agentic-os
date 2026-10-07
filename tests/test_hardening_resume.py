"""Resume hint: a retry after an interruption mid-run is told to inspect state first
instead of blindly redoing the step (Kiro Crew: TaskRunner resume_hint)."""

import pytest

from aos.board import Board
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.workers.common import prepare_run


@pytest.fixture
def setup(configured, data, git_repo, tmp_path):
    proj = git_repo(tmp_path / "api")
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(proj), "targets": ["claude"]}}})
    AOS(configured).register_project("api")
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    t = b.add_task("checkout", "api", "Orders", max_attempts=5)
    b.promote()
    return AOS(configured, slug="api"), b, t, proj


def prompt_for(aos, b, t, proj, tmp_path):
    spec, _ = prepare_run(aos, b, b.task(t), tmp_path / "wt", proj, "/bin/aos")
    return spec.prompt


def test_interrupted_attempt_sets_hint_until_completion(setup, tmp_path):
    aos, b, t, proj = setup
    b.claim(t)
    assert "may have partly run" not in prompt_for(aos, b, t, proj, tmp_path)
    b.attempt_failed(t, "timed out after 45 min")
    b.claim(t)
    p = prompt_for(aos, b, t, proj, tmp_path)
    assert "may have partly run" in p and "timed out after 45 min" in p and "git status" in p
    b.mark_seen(t)
    b.complete(t, "done", author="w")
    assert b.task(t)["resume_hint"] == 0


def test_hint_survives_a_human_retry(setup, tmp_path):
    aos, b, t, proj = setup
    b.claim(t)
    b.attempt_failed(t, "exited with code 1")
    b.cancel(t)
    b.retry(t, note="try again")
    b.claim(t)
    p = prompt_for(aos, b, t, proj, tmp_path)
    assert "may have partly run" in p and "try again" in p


def test_no_hint_when_nothing_ran(setup, tmp_path):
    aos, b, t, proj = setup
    b.claim(t)
    b.attempt_failed(t, "launch failed: no such file", ran=False)
    b.claim(t)
    assert "may have partly run" not in prompt_for(aos, b, t, proj, tmp_path)
