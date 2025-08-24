# app/agent/mcp_client.py
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

try:
    import httpx
    HAS_MCP_HTTP = True
except Exception:
    httpx = None  # type: ignore
    HAS_MCP_HTTP = False

# Optional stdio client (not required for HTTP-only)
try:
    # If you have or add a stdio client, import it here.
    HAS_MCP_STDIO = False
except Exception:
    HAS_MCP_STDIO = False


class HttpMCPClient:
    """Minimal HTTP client for an MCP façade:
       GET  /health
       GET  /tools
       POST /call
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        if not HAS_MCP_HTTP:
            raise RuntimeError("httpx not installed; HTTP MCP unavailable.")
        self.base = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout, headers={
                                        "Content-Type": "application/json"})

    async def health(self) -> Dict[str, Any]:
        r = await self.client.get(f"{self.base}/health")
        r.raise_for_status()
        return r.json()

    async def list_tools(self) -> Any:
        r = await self.client.get(f"{self.base}/tools")
        r.raise_for_status()
        # Some facades return {"tools":[...]}, others might embed content
        return r.json()

    async def call(self, tool: str, args: Dict[str, Any]) -> Any:
        """Try multiple payload shapes to maximize compatibility."""
        attempts: List[Tuple[str, Dict[str, Any]]] = [
            ("name_arguments",    {"name": tool, "arguments": args}),
            ("tool_arguments",    {"tool": tool, "arguments": args}),
            ("tool_args",         {"tool": tool, "args": args}),
        ]

        last_err: Optional[Exception] = None
        for label, payload in attempts:
            try:
                resp = await self.client.post(f"{self.base}/call", json=payload)
                if 200 <= resp.status_code < 300:
                    # Success
                    return resp.json()
                # 400/422 are most likely schema mismatches; try next shape
                if resp.status_code in (400, 422):
                    last_err = httpx.HTTPStatusError(
                        f"{resp.status_code} for payload shape '{label}': {resp.text}",
                        request=resp.request, response=resp
                    )
                    continue
                # Other HTTP errors: raise
                resp.raise_for_status()
            except Exception as e:  # network, JSON, etc.
                last_err = e
                continue

        # If we got here, all shapes failed
        if last_err:
            raise last_err
        raise RuntimeError("MCP /call failed with unknown error")

    async def close(self):
        await self.client.aclose()


class MCPManager:
    """Orchestrates multiple MCP servers (HTTP and optionally stdio)."""

    def __init__(self):
        self.http_clients: Dict[str, HttpMCPClient] = {}
        # self.stdio_clients: Dict[str, ...] = {}
        self.default_name: Optional[str] = None

    # ---------- HTTP ----------
    async def add_http(self, name: str, base_url: str):
        if name in self.http_clients:
            # Replace if re-adding
            try:
                await self.http_clients[name].close()
            except Exception:
                pass
        self.http_clients[name] = HttpMCPClient(base_url)
        # Set default if none
        if not self.default_name:
            self.default_name = name

    # ---------- stdio (placeholder) ----------
    async def add_stdio(self, name: str, command: str, env: Optional[Dict[str, str]] = None):
        raise RuntimeError("MCP stdio client not implemented in this build.")

    # ---------- common ----------
    def list_servers(self) -> List[str]:
        names = set(self.http_clients.keys())
        # names |= set(self.stdio_clients.keys())
        return sorted(names)

    def set_default(self, name: str):
        if name not in self.http_clients:  # and name not in self.stdio_clients
            raise RuntimeError(f"No such MCP server: {name}")
        self.default_name = name

    def _resolve(self, name: Optional[str]) -> Tuple[str, str]:
        # Returns a tuple (kind, name): kind = "http" | "stdio"
        if name is None:
            if not self.default_name:
                raise RuntimeError("No default MCP server set.")
            name = self.default_name
        if name in self.http_clients:
            return ("http", name)
        # if name in self.stdio_clients:
        #     return ("stdio", name)
        raise RuntimeError(f"No such MCP server: {name}")

    async def list_tools(self, name: Optional[str] = None) -> Any:
        kind, n = self._resolve(name)
        if kind == "http":
            return await self.http_clients[n].list_tools()
        # stdio path would go here
        raise RuntimeError("Unsupported MCP kind.")

    async def call(self, tool: str, args: Dict[str, Any], server_name: Optional[str] = None) -> Any:
        kind, n = self._resolve(server_name)
        if kind == "http":
            return await self.http_clients[n].call(tool, args)
        # stdio path would go here
        raise RuntimeError("Unsupported MCP kind.")

    async def remove(self, name: str):
        if name in self.http_clients:
            try:
                await self.http_clients[name].close()
            finally:
                del self.http_clients[name]
                if self.default_name == name:
                    self.default_name = self.list_servers(
                    )[0] if self.list_servers() else None
            return
        # if name in self.stdio_clients: ...
        raise RuntimeError(f"No such MCP server: {name}")

    async def close_all(self):
        for c in list(self.http_clients.values()):
            try:
                await c.close()
            except Exception:
                pass
        self.http_clients.clear()
        self.default_name = None


# Single instance used by the REPL
mcp_manager = MCPManager()
