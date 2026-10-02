"""Fake graphskill MCP server for tests: answers from a tiny in-memory 'graph' of --root."""

import sys

try:
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:
    from mcp.server.fastmcp import FastMCP as Server

root = sys.argv[sys.argv.index("--root") + 1]
mcp = Server("graphskill")


@mcp.tool()
def repo_map(budget_tokens: int = 2000) -> list[str]:
    """Top symbols."""
    return [f"{root}: OrdersController.create", "OrderService.place"]


@mcp.tool()
def search_symbols(query: str) -> list[dict]:
    """Find symbols by name."""
    return [{"name": query, "path": "src/orders.py", "line": 12}]


@mcp.tool()
def boom() -> str:
    """Always fails."""
    raise RuntimeError("graph exploded")


mcp.run()
