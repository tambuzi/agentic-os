import asyncio
import json
import sys
from pathlib import Path

import pytest

from aos import codegraph
from aos.config import load_user_config, save_user_config
from aos.core import AOS
from aos.errors import AosError
from aos.mcp_server import Tools

FAKE = Path(__file__).parent / "fake_graph_server.py"


def with_graph(project: Path) -> Path:
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"graphskill": {
        "command": sys.executable, "args": [str(FAKE), "--root", str(project)]}}}))
    return project


@pytest.fixture
def projects(configured, tmp_path):
    api, web = tmp_path / "api", tmp_path / "web"
    api.mkdir()
    web.mkdir()
    with_graph(api)
    save_user_config({**load_user_config(), "projects": {
        "api": {"path": str(api), "targets": ["claude"]}, "web": {"path": str(web), "targets": ["claude"]}}})
    return api, web


def test_graph_server_lookup(projects):
    api, web = projects
    assert codegraph.graph_server(api)["command"] == sys.executable
    assert codegraph.graph_server(web) is None


def test_list_tools_and_query(projects):
    api, _ = projects
    names = [t["name"] for t in codegraph.list_tools(api)]
    assert {"repo_map", "search_symbols"} <= set(names)
    r = codegraph.query(api, "search_symbols", {"query": "OrderService"})
    assert "OrderService" in json.dumps(r["result"]) and "src/orders.py" in json.dumps(r["result"])
    assert str(api) in json.dumps(codegraph.query(api, "repo_map")["result"])


def test_query_errors_are_clear(projects, tmp_path):
    api, web = projects
    with pytest.raises(AosError, match="boom failed"):  # the server library hides the exception text
        codegraph.query(api, "boom")
    with pytest.raises(AosError, match="nope"):
        codegraph.query(api, "nope")
    with pytest.raises(AosError, match="no graphskill"):
        codegraph.query(web, "repo_map")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".mcp.json").write_text(json.dumps({"mcpServers": {"graphskill": {"command": "/no/such/graphskill"}}}))
    with pytest.raises(AosError, match="could not start"):
        codegraph.list_tools(broken, timeout=10)


def test_mcp_tools_reach_other_projects(projects):
    t = Tools(AOS(projects[0].parent.parent / "agenticOS", slug="web"))
    r = t.code_query("api", "search_symbols", {"query": "OrdersController"})
    assert "OrdersController" in json.dumps(r)
    assert "repo_map" in [x["name"] for x in t.code_tools("api")["tools"]]
    e = t.code_query("web", "repo_map")
    assert "error" in e and "aos link" in e.get("hint", "")
    assert "not linked" in t.code_query("ghost", "repo_map")["error"]


def test_works_from_inside_a_running_event_loop(projects):
    t = Tools(AOS(projects[0].parent.parent / "agenticOS"))

    async def inside_server():
        return t.code_query("api", "repo_map")

    r = asyncio.run(inside_server())
    assert "error" not in r and "OrdersController" in json.dumps(r)


def test_decode_multiple_text_items_as_list():
    from types import SimpleNamespace as NS
    two = NS(structuredContent=None, content=[NS(text='{"name": "A"}'), NS(text='{"name": "B"}')])
    assert codegraph._decode(two) == [{"name": "A"}, {"name": "B"}]
    one = NS(structuredContent=None, content=[NS(text='{"name": "A"}')])
    assert codegraph._decode(one) == {"name": "A"}
    plain = NS(structuredContent=None, content=[NS(text="not json"), NS(text="still not")])
    assert codegraph._decode(plain) == "not json\nstill not"
    structured = NS(structuredContent={"result": [1, 2]}, content=[])
    assert codegraph._decode(structured) == [1, 2]


def test_wrong_arguments_error_names_expected_parameters(projects):
    api, _ = projects
    with pytest.raises(AosError) as e:
        codegraph.query(api, "search_symbols", {"symbol": "OrderService"})
    assert "query" in e.value.hint
    tools = {t["name"]: t for t in codegraph.list_tools(api)}
    assert "query" in tools["search_symbols"]["parameters"]
