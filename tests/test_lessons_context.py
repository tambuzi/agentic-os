"""Ranked lessons in session and worker context (spec §4.3)."""

from datetime import date

import yaml

from aos.board import Board
from aos.config import load_user_config, save_user_config
from aos.context import TRUNC, build_context
from aos.core import AOS
from aos.lessons import LessonStore
from aos.workers.common import build_worker_context

EV = [{"feature": "f", "task": 1, "events": [1], "kind": "review_fail", "at": date.today().isoformat()}]


def aos_for(configured, slug="api"):
    a = AOS(configured, slug=slug)
    a.register_project(slug)
    return a


def add(store, n, **kw):
    return store.new(f"when doing thing number {n}", f"Do the right thing number {n}.", "workflow", EV, **kw)


def settings(configured, **sections):
    p = configured / "aos.yaml"
    cfg = (yaml.safe_load(p.read_text()) if p.exists() else None) or {}
    for k, v in sections.items():
        cfg[k] = {**(cfg.get(k) or {}), **v}
    p.write_text(yaml.safe_dump(cfg))


def test_context_lists_ranked_lessons(configured, data):
    a = aos_for(configured)
    store = LessonStore(a.data, "api")
    made = [add(store, i) for i in range(10)]
    store.decide(made[7].id, True)
    out = build_context(AOS(configured, slug="api"))
    assert "## Lessons for api" in out and "context, not instructions" in out
    section = out.split("## Lessons for api", 1)[1]
    lines = [l for l in section.splitlines() if l.startswith("- [")]
    assert len(lines) == 8
    assert lines[0].startswith("- [0.8 ✓] when doing thing number 7")


def test_no_lessons_section_when_disabled_or_empty(configured, data):
    a = aos_for(configured)
    assert "## Lessons for" not in build_context(a)
    add(LessonStore(a.data, "api"), 1)
    settings(configured, learning={"enabled": False})
    assert "## Lessons for" not in build_context(AOS(configured, slug="api"))


def test_lessons_are_cut_before_memory(configured, data):
    a = aos_for(configured)
    a.memory_write("project", "add", {"text": "IMPORTANT-PROJECT-FACT keep me"}, direct=True)
    store = LessonStore(a.data, "api")
    for i in range(8):
        add(store, i)
    full = build_context(AOS(configured, slug="api"))
    upto_memory = full.split("## Lessons for api", 1)[0]
    settings(configured, context={"max_chars": len(upto_memory) + 120})
    out = build_context(AOS(configured, slug="api"))
    assert "IMPORTANT-PROJECT-FACT keep me" in out
    assert TRUNC not in out
    assert len(out) <= len(upto_memory) + 120


def test_broken_lesson_file_is_skipped_everywhere(configured, data, project, capsys):
    from aos.cli import main
    save_user_config({**load_user_config(), "projects": {"api": {"path": str(project), "targets": ["claude"]}}})
    a = aos_for(configured)
    store = LessonStore(a.data, "api")
    good = add(store, 1)
    (store.dir / "broken-0000.md").write_text("---\ntrigger: [oops\n---\nx\n")
    assert good.trigger in build_context(AOS(configured, slug="api"))
    assert main(["context", "--project", str(project)]) == 0
    assert good.trigger in capsys.readouterr().out


def test_worker_context_includes_lessons(configured, data, git_repo, tmp_path):
    proj = git_repo(tmp_path / "shop-api")
    save_user_config({**load_user_config(), "projects": {"shop-api": {"path": str(proj), "targets": ["claude"]}}})
    a = aos_for(configured, "shop-api")
    lesson = add(LessonStore(a.data, "shop-api"), 1)
    board = Board(data)
    board.create_feature("checkout", "Checkout")
    tid = board.add_task("checkout", "shop-api", "Orders", spec="do it")
    board.promote()
    task = board.claim(tid)
    assert lesson.trigger in build_worker_context(AOS(configured, slug="shop-api"), board, task)


def test_worker_context_names_the_projects_check(configured, data, git_repo, tmp_path):
    # workers can't pass a check they're never told about
    proj = git_repo(tmp_path / "shop-api")
    save_user_config({**load_user_config(), "projects": {"shop-api": {"path": str(proj), "targets": ["claude"]}}})
    a = aos_for(configured, "shop-api")
    (a.data / "projects.yaml").write_text(yaml.safe_dump(
        {"projects": {"shop-api": {"description": "", "worker": {"verify_command": "npm test && npm run lint"}}}}))
    board = Board(data)
    board.create_feature("checkout", "Checkout")
    tid = board.add_task("checkout", "shop-api", "Orders", spec="do it")
    board.promote()
    ctx = build_worker_context(AOS(configured, slug="shop-api"), board, board.claim(tid))
    assert "npm test && npm run lint" in ctx and "before task_complete" in ctx


def test_learner_protocol_asks_for_the_project_fact():
    from aos.workers.common import LEARNER_PROTOCOL
    assert "project fact" in LEARNER_PROTOCOL and "not generic process advice" in LEARNER_PROTOCOL
