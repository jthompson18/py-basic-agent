# tests/test_mcp_client.py
import pytest
import respx
import httpx

pytestmark = pytest.mark.asyncio


def _tools_payload():
    return {"tools": [
        {"name": "list_files", "description": "List files"},
        {"name": "read_text", "description": "Read text"},
        {"name": "write_text", "description": "Write text"},
        {"name": "stat", "description": "Stat"},
    ]}


@respx.mock
async def test_mcp_http_add_list_call(monkeypatch):
    # Mock HTTP endpoints for the MCP facade
    base = "http://mcp.test:8765"
    respx.get(
        f"{base}/tools").mock(return_value=httpx.Response(200, json=_tools_payload()))
    respx.post(f"{base}/call").mock(return_value=httpx.Response(200, json={
        "content": [{"type": "json", "value": {"ok": True, "files": ["a", "b"]}}]
    }))

    from agent.mcp_client import MCPManager
    mgr = MCPManager()
    await mgr.add_http("fs", base)

    tl = await mgr.list_tools("fs")
    # accept either list or dict shape
    if isinstance(tl, dict):
        assert "tools" in tl and len(tl["tools"]) == 4
    else:
        assert isinstance(tl, list) and len(tl) >= 1

    res = await mgr.call("list_files", {"path": "."}, server_name="fs")
    # facade may return dict; just check structure
    assert isinstance(res, dict) and "content" in res

    await mgr.remove("fs")
    await mgr.close_all()
