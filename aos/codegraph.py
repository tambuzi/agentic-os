"""Query any linked project's graphskill server from any session.

graphskill serves one repo per session by design (each repo's .mcp.json runs
`graphskill serve <that repo>`), so a planner opened in project A cannot see
B's graph. aos bridges that: it starts B's graphskill server as a short-lived
MCP client, calls one tool, and shuts it down.
"""

from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from .errors import AosError
from .store import read_text


def graph_server(project_path: str | Path) -> dict | None:
    """The project's `graphskill` MCP server entry from its .mcp.json, if any."""
    try:
        data = json.loads(read_text(Path(project_path) / ".mcp.json") or "{}")
    except json.JSONDecodeError:
        return None
    server = (data.get("mcpServers") or {}).get("graphskill")
    return server if isinstance(server, dict) and server.get("command") else None


def _require_server(project_path: Path) -> dict:
    server = graph_server(project_path)
    if not server:
        raise AosError(f"no graphskill server configured for {project_path}",
                       "install graphskill, then re-run `aos link` for that project")
    return server


async def _session_call(project_path: Path, server: dict, fn):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=server["command"], args=[str(a) for a in server.get("args") or []],
        env={**os.environ, **{k: str(v) for k, v in (server.get("env") or {}).items()}},
        cwd=str(project_path))
    with open(os.devnull, "w") as quiet:  # keep the child's stderr out of our MCP stdout/stderr
        async with stdio_client(params, errlog=quiet) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)


def _run(project_path: Path, fn, timeout: float):
    """Run an MCP client call on its own event loop in a worker thread: safe even when the
    caller is itself running inside an event loop (e.g. our own MCP server)."""
    server = _require_server(project_path)

    def target():
        return asyncio.run(asyncio.wait_for(_session_call(project_path, server, fn), timeout))

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(target)
        try:
            return future.result(timeout=timeout + 5)
        except AosError:
            raise
        except (FutureTimeout, asyncio.TimeoutError, TimeoutError):
            raise AosError(f"graphskill for {project_path} did not answer within {timeout:g}s",
                           "is the project indexed? run `graphskill index <path>`")
        except Exception as e:  # spawn failure, protocol error, crash
            inner = _innermost(e)
            if isinstance(inner, AosError):  # our own error, wrapped by the client's task group
                raise inner
            raise AosError(f"could not start or talk to graphskill for {project_path}: {_root_cause(e)}",
                           "check the graphskill entry in the project's .mcp.json (aos doctor)")


def _innermost(e: BaseException) -> BaseException:
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return e


def _root_cause(e: BaseException) -> str:
    e = _innermost(e)
    return f"{type(e).__name__}: {e}"


def _parameters(tool) -> dict:
    """{name: "type, required"} from a tool's JSON input schema."""
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {}
    required = set(schema.get("required") or [])
    out = {}
    for name, prop in (schema.get("properties") or {}).items():
        kind = prop.get("type") or "/".join(x.get("type", "?") for x in prop.get("anyOf", [])) or "any"
        out[name] = f"{kind}{', required' if name in required else ''}"
    return out


def list_tools(project_path: str | Path, timeout: float = 60) -> list[dict]:
    async def fn(session):
        r = await session.list_tools()
        return [{"name": t.name, "description": (t.description or "").strip(), "parameters": _parameters(t)}
                for t in r.tools]

    return _run(Path(project_path), fn, timeout)


def _decode(result) -> object:
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if structured is not None:
        return structured.get("result", structured) if isinstance(structured, dict) else structured
    texts = [c.text for c in (getattr(result, "content", None) or []) if getattr(c, "text", None) is not None]
    try:  # mcp 1.x servers send a list result as one text item per element
        parsed = [json.loads(x) for x in texts]
    except json.JSONDecodeError:
        return "\n".join(texts)
    return parsed[0] if len(parsed) == 1 else parsed


def query(project_path: str | Path, tool: str, arguments: dict | None = None, timeout: float = 120) -> dict:
    async def fn(session):
        tools = {t.name: t for t in (await session.list_tools()).tools}
        if tool not in tools:
            raise AosError(f"graphskill has no tool {tool!r}", "available: " + ", ".join(sorted(tools)))
        r = await session.call_tool(tool, arguments or {})
        if getattr(r, "isError", False) or getattr(r, "is_error", False):
            params = _parameters(tools[tool])
            hint = ("expected arguments: " + "; ".join(f"{k} ({v})" for k, v in params.items())) if params \
                else "this tool takes no arguments"
            raise AosError(f"graphskill {tool} failed: {_decode(r)}", hint)
        return {"tool": tool, "result": _decode(r)}

    return _run(Path(project_path), fn, timeout)
