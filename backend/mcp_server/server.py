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

The principal threaded into each `tools/call` is JWT-derived (`Principal(source="mcp")`).
One shared server serves every HTTP session, so the principal cannot be bound at build
time: an ASGI step (see __main__.py) verifies the bearer token per request and publishes
the principal on `_REQUEST_PRINCIPAL` (a ContextVar) for that request's scope; `call_tool`
reads it, falling back to the (normally unset) `build_server(principal=...)` arg.

Without a principal `identity` and `search_medical_knowledge` should work; the Core refuses
the principal-scoped `search_user_documents` with a JSON-RPC error (`PrincipalRequiredError`).

`ToolInputError` (and its subclass `PrincipalRequiredError`) map to a proper JSON-RPC
error via `McpError` — never a 200 response carrying an error string.
"""
import contextlib, contextvars
from typing import Iterator, List, Optional

from mcp.server.lowlevel import Server
from mcp.shared.exceptions import McpError
from mcp import types

from toolcore.contracts import Principal, ToolInputError
from toolcore.core import get_mcp_client


SERVER_NAME = "documedai-mcp"

# Per-request principal: a single server instance is shared across all HTTP sessions, so
# the caller identity is published here per request rather than bound onto the server.
_REQUEST_PRINCIPAL: contextvars.ContextVar[Optional[Principal]] = contextvars.ContextVar(
    "mcp_request_principal", default = None
)


@contextlib.contextmanager
def request_principal(principal: Optional[Principal]) -> Iterator[None]:
    """Bind `principal` for the current request scope; always reset on exit."""
    token = _REQUEST_PRINCIPAL.set(principal)
    try:
        yield
    finally:
        _REQUEST_PRINCIPAL.reset(token)


def build_server(principal: Optional[Principal] = None) -> Server:
    """
    Construct the MCP server bound to the shared Tool Core.

    `principal` is a build-time fallback (normally None); the HTTP transport instead
    publishes a per-request principal via `request_principal(...)`, which `call_tool` prefers.
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
        # HTTP publishes a per-request principal on the ContextVar; otherwise fall back to
        # the (normally unset) build-time `principal`.
        effective = _REQUEST_PRINCIPAL.get() or principal
        try:
            result = core.call_tool(name, arguments or {}, principal = effective)
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
