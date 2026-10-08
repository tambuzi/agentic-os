"""`aos acp install claude` and what `aos doctor` says about ACP (spec §6)."""

import subprocess

import pytest

from aos.acp import tools
from aos.cli import main
from aos.config import load_user_config, save_user_config


def run(argv, capsys):
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def npm_calls(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(tools, "run_command", fake_run)
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}")
    return calls


def test_install_claude_pins_the_adapter_under_aos_home(configured, home, npm_calls, capsys):
    code, out, _ = run(["acp", "install", "claude"], capsys)
    assert code == 0
    assert npm_calls == [["/usr/bin/npm", "install", "--prefix", str(home / "acp"), "--no-save",
                          "@agentclientprotocol/claude-agent-acp@0.87.0"]]
    assert "claude-agent-acp" in out


def test_install_without_npm_is_a_clear_error(configured, monkeypatch, capsys):
    monkeypatch.setattr("shutil.which", lambda n: None)
    code, _, err = run(["acp", "install", "claude"], capsys)
    assert code != 0 and "npm" in err


def test_install_kiro_needs_nothing(configured, npm_calls, capsys):
    code, out, _ = run(["acp", "install", "kiro"], capsys)
    assert code == 0 and npm_calls == [] and "kiro-cli acp" in out


def test_doctor_reports_acp_per_tool_and_the_global_worker(configured, home, monkeypatch, capsys):
    monkeypatch.setattr("shutil.which", lambda n: f"/usr/bin/{n}" if n in ("node", "claude", "kiro-cli") else None)
    save_user_config({**load_user_config(), "worker": "kiro"})
    code, out, _ = run(["doctor"], capsys)
    assert "board work uses kiro (global (aos worker))" in out
    assert "node" in out
    assert "claude over ACP unavailable" in out and "aos acp install claude" in out  # adapter not installed
    assert "kiro: acp" in out

    entry = tools.default_claude_adapter(home)
    entry.parent.mkdir(parents=True)
    entry.write_text("// adapter")
    code, out, _ = run(["doctor"], capsys)
    assert "claude: acp" in out
