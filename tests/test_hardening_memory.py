"""Parallelism sized by memory: the parallel number is a ceiling; each start also needs
enough free memory left after it (Kiro Crew: memory, not a count, bounds concurrency)."""

import sys
from pathlib import Path

import pytest
import yaml

from aos import sysmem
from aos.board import Board
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.dispatcher import Dispatcher

ROOT = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).parent / "fake_worker.py"


def test_reads_free_memory_on_this_machine():
    gb = sysmem.available_gb()
    assert gb is None or gb > 0


def test_parses_vm_stat_and_meminfo():
    vm = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
          "Pages free:                               65536.\nPages active:  1.\n"
          "Pages inactive:                           65536.\nPages speculative:     0.\n")
    assert sysmem._from_vm_stat(vm) == pytest.approx(2.0)
    assert sysmem._from_meminfo("MemTotal: 16000000 kB\nMemAvailable:    3145728 kB\n") == pytest.approx(3.0)


@pytest.fixture
def env(configured, data, git_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    monkeypatch.setenv("FAKE_MODE", "sleep")
    projects = {}
    for name in ("api", "web", "billing", "ops"):
        projects[name] = {"path": str(git_repo(tmp_path / name)), "targets": ["claude"]}
        AOS(configured).register_project(name)
    save_user_config({**load_user_config(), "projects": projects})
    b = Board(data)
    b.create_feature("checkout", "Checkout")
    for name in projects:
        b.add_task("checkout", name, "t", worker="fake")

    def write(board_cfg):
        (configured / "aos.yaml").write_text(yaml.safe_dump({
            "board": {"default_worker": "fake", **board_cfg},
            "workers": {"fake": {"adapter": "command", "timeout_min": 0.5,
                                 "command": [sys.executable, str(FAKE), "{task_id}"]}}}))
    return configured, write


def run_tick(repo, monkeypatch, free_gb):
    monkeypatch.setattr("aos.dispatcher.available_gb", lambda: free_gb)
    d = Dispatcher(repo, tick=0.05)
    d.tick()
    n = len(d.running)
    for r in list(d.running.values()):
        d._terminate(r)
    d._reap()
    return n, d


def test_auto_uses_max_parallel_when_memory_is_plentiful(env, monkeypatch):
    repo, write = env
    write({"parallel": "auto", "max_parallel": 3})
    assert run_tick(repo, monkeypatch, 64.0)[0] == 3


def test_low_memory_holds_starts_but_never_all(env, monkeypatch):
    repo, write = env
    write({"parallel": "auto", "max_parallel": 6, "worker_memory_gb": 1.0, "min_free_memory_gb": 2.0})
    assert run_tick(repo, monkeypatch, 2.5)[0] == 1     # one always starts, the rest wait
    assert run_tick(repo, monkeypatch, 4.0)[0] == 2     # 4 - 1 >= 2, but with 1 running: 4 - 1 - 1 < 2


def test_fixed_number_and_unknown_memory(env, monkeypatch):
    repo, write = env
    write({"parallel": 2})
    assert run_tick(repo, monkeypatch, None)[0] == 2
    assert run_tick(repo, monkeypatch, 64.0)[0] == 2
