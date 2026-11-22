"""Keeps one MCP client session open for the app's lifetime and exposes the server's
tools to the agent."""

import json
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import Settings

log = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict


class MCPToolbox:
    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self.tools: list[ToolSpec] = []

    async def start(self, settings: Settings) -> None:
        if not settings.mcp_enabled:
            return
        try:
            stack = AsyncExitStack()
            params = StdioServerParameters(
                command=settings.mcp_server_command, args=settings.mcp_server_args, env=dict(os.environ)
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            listed = await session.list_tools()
            self.tools = [ToolSpec(t.name, t.description or "", t.input_schema or {}) for t in listed.tools]
            self._stack, self._session = stack, session
            log.info("MCP connected: %s", [t.name for t in self.tools])
        except Exception:
            log.exception("Could not start the MCP server; the agent will run without its tools")
            self.tools = []

    async def call(self, name: str, arguments: dict) -> str:
        if self._session is None:
            raise RuntimeError("MCP server is not connected")
        result = await self._session.call_tool(name, arguments)
        if getattr(result, "structured_content", None) is not None:
            text = json.dumps(result.structured_content, default=str)
        else:
            text = "\n".join(getattr(c, "text", "") for c in result.content)
        if result.is_error:
            raise RuntimeError(text or f"Tool {name} failed")
        return text

    async def stop(self) -> None:
        if self._stack:
            await self._stack.aclose()
        self._stack = self._session = None


toolbox = MCPToolbox()
