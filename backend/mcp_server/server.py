"""
MCP server wrapping the shared Tool Core.

Builds a low-level `mcp.server.lowlevel.Server` whose two handlers are thin bridges
onto the Phase-1 core:

    tools/list  ->  ToolCore.list_tools()   (each spec's input_schema advertised verbatim)
    tools/call  ->  ToolCore.call_tool(...)  ->  ToolResult  ->  [TextContent]

The low-level Server is used (not FastMCP's `@tool` decorators) because our input
schemas are already authored as Pydantic-derived JSON Schema in `toolcore.contracts`
and carried on each `ToolSpec.input_schema`. FastMCP re-derives schemas from Python
type hints, which would fork the single source of truth; here we hand the SDK the
core's schema directly.

Issue 2.1 scope: NO auth yet — every call passes `principal=None`. That means
`identity` and `search_medical_knowledge` work, while the principal-scoped
`search_user_documents` is correctly refused by the Core with a JSON-RPC error (it
raises `PrincipalRequiredError`). JWT-derived principals arrive in Issue 2.2.

`ToolInputError` (and its subclass `PrincipalRequiredError`) map to a proper JSON-RPC
error via `McpError` — never a 200 response carrying an error string.
"""
from typing import List, Optional

from mcp.server.lowlevel import Server
from mcp.shared.exceptions import McpError
from mcp import types

from toolcore.contracts import Principal, ToolInputError
from toolcore.core import get_mcp_client


SERVER_NAME = "documedai-mcp"


def build_server(principal: Optional[Principal] = None) -> Server:
    """
    Construct the MCP server bound to the shared Tool Core.

    `principal` is threaded into every `tools/call`. In Issue 2.1 it is always None
    (no auth surface yet); Issue 2.2 replaces this with a per-session, JWT-derived
    `Principal(source="mcp")`.
    """
    core = get_mcp_client()
    server: Server = Server(SERVER_NAME)

    @server.list_tools()
    async def list_tools() -> List[types.Tool]:
        return [
            types.Tool(
                name = spec.name,
                title = spec.title,
                description = spec.description,
                inputSchema = spec.input_schema,
            )
            for spec in core.list_tools()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> List[types.ContentBlock]:
        try:
            result = core.call_tool(name, arguments or {}, principal = principal)
        except ToolInputError as exc:
            # Covers PrincipalRequiredError too (it subclasses ToolInputError): bad args,
            # unknown tool, and missing-principal all surface as a real JSON-RPC error
            # (INVALID_PARAMS) rather than a success response wrapping an error string.
            raise McpError(
                types.ErrorData(code = types.INVALID_PARAMS, message = str(exc))
            ) from exc

        return [
            types.TextContent(type = "text", text = block["text"])
            for block in result.content
            if block.get("type") == "text"
        ]

    return server
