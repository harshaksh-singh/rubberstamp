"""Serve a world session over MCP with FastMCP, generated from the same registry as the Python API.

Each registered tool becomes a `SessionTool`: its MCP input schema is the ToolSpec's JSON Schema, its
annotations come from the ToolSpec (readOnlyHint for reads, idempotentHint, ...), and running it
just forwards to `WorldSession.call`. MCP clients and in-process callers share one code path, which
the parity test checks digest for digest.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError as McpToolError
from fastmcp.tools.base import Tool, ToolResult
from mcp.types import ToolAnnotations
from pydantic import Field
from wf_core.models import ToolSpec

from .catalog import HELPDESK_APP
from .session import WorldSession
from .tools import REGISTRY


class SessionTool(Tool):
    session: Any = Field(default=None, exclude=True)  # the WorldSession that executes the call

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        call = self.session.call(self.name, arguments)
        if not call.ok:
            raise McpToolError(call.error or f"{self.name} failed")
        return ToolResult(structured_content=call.result)


def mcp_annotations(spec: ToolSpec) -> ToolAnnotations:
    read = spec.effect == "read"
    return ToolAnnotations(
        read_only_hint=read,
        destructive_hint=not read,
        idempotent_hint=spec.idempotent,
        open_world_hint=False,
    )


def build_mcp(session: WorldSession, name: str = HELPDESK_APP) -> FastMCP:
    """An MCP server exposing the tools this session allows, all bound to this session's world."""
    server = FastMCP(name)
    for spec in REGISTRY.specs():
        if spec.name in session.allowed:
            server.add_tool(
                SessionTool(
                    name=spec.name,
                    description=spec.description,
                    parameters=spec.params,
                    annotations=mcp_annotations(spec),
                    session=session,
                )
            )
    return server
