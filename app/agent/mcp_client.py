from __future__ import annotations
import asyncio
import os
import shlex
from typing import Any, Dict, List, Optional

HAS_MCP = True
try:
    # Expected modern MCP Python client
    from mcp import StdioServer, ClientSession  # type: ignore
except Exception:
    HAS_MCP = False


class MCPConnection:
    """
    Simple stdio MCP connection wrapper.
    """

    def __init__(self, name: str, command: str, env: Optional[Dict[str, str]] = None):
        self.name = name
        self.command = command
        self.env = env or {}
        self._server = None
        self._session = None

    async def start(self):
        if not HAS_MCP:
            raise RuntimeError(
                "MCP Python client not installed. Add `mcp` to requirements.txt and rebuild.")
        cmd = shlex.split(self.command)
        # Launch MCP server over stdio
        # type: ignore[attr-defined]
        self._server = await StdioServer.create(command=cmd, env={**os.environ, **self.env})
        # type: ignore[attr-defined]
        self._session = await ClientSession.create(self._server)

    async def stop(self):
        try:
            if self._session:
                await self._session.close()
        finally:
            self._session = None
            if self._server:
                await self._server.close()
            self._server = None

    async def list_tools(self) -> List[Dict[str, Any]]:
        if not self._session:
            raise RuntimeError("MCP connection not started")
        # returns [{'name':'toolName','description':'...','input_schema':{...}}, ...]
        return await self._session.list_tools()  # type: ignore[attr-defined]

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        if not self._session:
            raise RuntimeError("MCP connection not started")
        # returns provider-defined result (structured)
        # type: ignore[attr-defined]
        return await self._session.call_tool(tool_name, arguments)


class MCPManager:
    """
    In-memory registry of MCP stdio servers.
    """

    def __init__(self):
        self._conns: Dict[str, MCPConnection] = {}
        self.default_name: Optional[str] = None

    async def add_stdio(self, name: str, command: str, env: Optional[Dict[str, str]] = None):
        if name in self._conns:
            raise ValueError(f"MCP server '{name}' already exists")
        conn = MCPConnection(name, command, env)
        await conn.start()
        self._conns[name] = conn
        if not self.default_name:
            self.default_name = name

    async def remove(self, name: str):
        conn = self._conns.pop(name, None)
        if conn:
            await conn.stop()
        if self.default_name == name:
            self.default_name = next(iter(self._conns), None)

    def list_servers(self) -> List[str]:
        return list(self._conns.keys())

    def set_default(self, name: str):
        if name not in self._conns:
            raise KeyError(f"No MCP server named '{name}'")
        self.default_name = name

    async def list_tools(self, name: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._require(name)
        return await conn.list_tools()

    async def call(self, tool: str, arguments: Dict[str, Any], server: Optional[str] = None) -> Any:
        conn = self._require(server)
        return await conn.call_tool(tool, arguments)

    def _require(self, name: Optional[str]) -> MCPConnection:
        effective = name or self.default_name
        if not effective or effective not in self._conns:
            raise KeyError(
                "No MCP server connected. Use `/mcp add -n <name> -c \"<command>\"` first.")
        return self._conns[effective]


# Global singleton
mcp_manager = MCPManager()
