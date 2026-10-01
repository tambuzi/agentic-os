import json
import shutil
import subprocess

import pytest
import yaml

from aos.config import load_user_config, save_user_config
from aos.errors import AosError
from aos.link import link, sync, unlink


def load(p):
    return json.loads(p.read_text())


@pytest.fixture
def env(configured, project, monkeypatch, make_skill):
    # aos.link and aos.config share the shutil module: one patch covers both
    monkeypatch.setattr("shutil.which", lambda n: "/bin/aos" if n == "aos" else None)
    make_skill("deploy-app", files={"scripts/run.sh": "echo hi\n"})
    make_skill("crm-only", projects=["crm"])
    return project


def test_link_writes_claude_and_kiro(env, configured):
    p = env
    rep = link(p, slug="shop")
    assert load(p / ".mcp.json")["mcpServers"]["aos"] == {"command": "aos", "args": ["serve", "--project", "."]}
    assert load(p / ".claude/settings.local.json")["hooks"]["SessionStart"] == [
        {"hooks": [{"type": "command", "command": '"/bin/aos" context --project "$CLAUDE_PROJECT_DIR"'}]}]
    assert (p / ".claude/skills/deploy-app/scripts/run.sh").read_text() == "echo hi\n"
    assert not (p / ".claude/skills/crm-only").exists()
    assert load(p / ".kiro/settings/mcp.json")["mcpServers"]["aos"]["command"] == "/bin/aos"
    kh = load(p / ".kiro/hooks/aos.json")
    assert kh["version"] == "v1" and kh["hooks"][0]["trigger"] == "SessionStart"
    assert (p / ".kiro/steering/aos.md").read_text().startswith("---\ninclusion: always\n---")
    assert (p / ".kiro/skills/deploy-app/SKILL.md").exists()
    gi = (p / ".gitignore").read_text()
    assert ".aos/" in gi and ".claude/skills/deploy-app/" in gi and ".kiro/skills/deploy-app/" in gi
    assert (configured / "memory/projects/shop.md").exists()
    assert "shop" in yaml.safe_load((configured / "projects.yaml").read_text())["projects"]
    assert load_user_config()["projects"]["shop"]["path"] == str(p.resolve())
    assert rep["graphskill"].startswith("graphskill:")


def test_link_is_idempotent(env):
    link(env, slug="shop")
    rep = link(env, slug="shop")
    assert rep["changed"] == [] and rep["removed"] == [] and rep["conflicts"] == []


def test_preserves_user_config(env):
    p = env
    (p / ".mcp.json").write_text(json.dumps({"mcpServers": {"db": {"command": "pg"}}}))
    (p / ".claude").mkdir()
    user_settings = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo mine"}]}]},
                     "permissions": {"allow": ["Bash(ls)"]}}
    (p / ".claude/settings.local.json").write_text(json.dumps(user_settings))
    (p / ".gitignore").write_text("node_modules/\n")
    link(p, slug="shop")
    unlink(p)
    assert load(p / ".mcp.json") == {"mcpServers": {"db": {"command": "pg"}}}
    assert load(p / ".claude/settings.local.json") == user_settings
    assert (p / ".gitignore").read_text() == "node_modules/\n"
    assert not (p / ".claude/skills").exists()
    assert not (p / ".kiro").exists()
    assert not (p / ".aos").exists()
    assert "shop" not in load_user_config()["projects"]


def test_user_skill_with_same_name_is_conflict(env):
    d = env / ".claude/skills/deploy-app"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("mine")
    rep = link(env, slug="shop")
    assert ".claude/skills/deploy-app" in rep["conflicts"]
    assert (d / "SKILL.md").read_text() == "mine"
    assert not (d / "scripts").exists()
    assert ".claude/skills/deploy-app/" not in (env / ".gitignore").read_text()
    unlink(env)
    assert (d / "SKILL.md").read_text() == "mine"


def test_sync_prunes_skill_deleted_upstream(env, configured):
    link(env, slug="shop")
    shutil.rmtree(configured / "skills/deploy-app")
    rep = sync(env)
    assert not (env / ".claude/skills/deploy-app").exists()
    assert ".claude/skills/deploy-app/SKILL.md" in rep["removed"]
    assert ".claude/skills/deploy-app/" not in (env / ".gitignore").read_text()


def test_malformed_json_aborts_cleanly(env):
    (env / ".mcp.json").write_text("{ not json")
    with pytest.raises(AosError) as e:
        link(env, slug="shop")
    assert ".mcp.json" in e.value.message
    assert (env / ".mcp.json").read_text() == "{ not json"
    assert not (env / ".claude").exists()
    assert "shop" not in load_user_config()["projects"]


def test_slug_taken_by_other_path(env, tmp_path):
    link(env, slug="shop")
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(AosError):
        link(other, slug="shop")


def test_targets_claude_only_then_add_kiro_then_drop_it(env):
    link(env, slug="shop", targets=["claude"])
    assert not (env / ".kiro").exists()
    link(env, slug="shop", targets=["claude", "kiro"])
    assert (env / ".kiro/hooks/aos.json").exists()
    link(env, slug="shop", targets=["claude"])
    assert not (env / ".kiro").exists()
    with pytest.raises(AosError):
        link(env, slug="shop", targets=["cursor"])


def test_graphskill_setup_and_kiro_mirror(env, monkeypatch):
    save_user_config({**load_user_config(), "graphskill_cmd": ["/gs/python", "-m", "graphskill"]})
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        (env / ".mcp.json").write_text(json.dumps({"mcpServers": {"graphskill": {"command": "gs", "args": ["serve"]}}}))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    rep = link(env, slug="shop", runner=runner)
    assert calls == [["/gs/python", "-m", "graphskill", "setup", str(env.resolve())]]
    assert rep["graphskill"] == "graphskill: ok"
    assert load(env / ".kiro/settings/mcp.json")["mcpServers"]["graphskill"] == {"command": "gs", "args": ["serve"]}
    assert set(load(env / ".mcp.json")["mcpServers"]) == {"graphskill", "aos"}


def test_graphskill_failure_is_reported_not_fatal(env):
    save_user_config({**load_user_config(), "graphskill_cmd": ["/gs/graphskill"]})

    def runner(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "No module named 'graphskill'")

    rep = link(env, slug="shop", runner=runner)
    assert "failed" in rep["graphskill"] and "No module named" in rep["graphskill"]
    assert (env / ".mcp.json").exists()


@pytest.mark.parametrize("rel,content", [
    (".kiro/settings/mcp.json", {"mcpServers": []}),
    (".claude/settings.local.json", {"hooks": []}),
    (".claude/settings.local.json", {"hooks": {"SessionStart": {"x": 1}}}),
    (".mcp.json", ["not", "an", "object"]),
])
def test_wrong_shape_json_aborts_cleanly(env, rel, content):
    (env / rel).parent.mkdir(parents=True, exist_ok=True)
    (env / rel).write_text(json.dumps(content))
    with pytest.raises(AosError) as e:
        link(env, slug="shop")
    assert rel in e.value.message
    assert not (env / ".claude/skills").exists() and not (env / ".aos").exists()
    assert "shop" not in load_user_config()["projects"]


def test_claude_mcp_entry_is_portable(env):
    link(env, slug="shop")
    assert load(env / ".mcp.json")["mcpServers"]["aos"] == {"command": "aos", "args": ["serve", "--project", "."]}


def test_teammates_committed_aos_entries_are_adopted(env):
    (env / ".mcp.json").write_text(json.dumps({"mcpServers": {"aos": {"command": "aos", "args": ["serve", "--project", "."]}}}))
    (env / ".kiro/settings").mkdir(parents=True)
    alice = {"command": "/Users/alice/.local/bin/aos", "args": ["serve", "--project", "/Users/alice/shop"]}
    (env / ".kiro/settings/mcp.json").write_text(json.dumps({"mcpServers": {"aos": alice}}))
    rep = link(env, slug="shop")
    assert rep["conflicts"] == []
    assert load(env / ".kiro/settings/mcp.json")["mcpServers"]["aos"]["args"] == ["serve", "--project", str(env.resolve())]


def test_file_modes_are_preserved(env, configured):
    (configured / "skills/deploy-app/scripts/run.sh").chmod(0o755)
    (env / ".gitignore").write_text("node_modules/\n")
    (env / ".gitignore").chmod(0o644)
    link(env, slug="shop")
    assert (env / ".gitignore").stat().st_mode & 0o777 == 0o644
    assert (env / ".claude/skills/deploy-app/scripts/run.sh").stat().st_mode & 0o777 == 0o755
    assert (env / ".mcp.json").stat().st_mode & 0o044  # group/other readable like any new file
