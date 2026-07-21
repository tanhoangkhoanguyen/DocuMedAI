"""
Entry point:  python -m mcp_server [--host --port]

Serves the Tool Core over MCP's streamable-HTTP transport (the current MCP HTTP
transport, not deprecated SSE), under Starlette at the /mcp path, via uvicorn.

External clients authenticate per request with `Authorization: Bearer <jwt>`
(verified in auth.py). Internal LangGraph callers never reach this process — they
use the in-process Tool Core path (`source="internal"`) directly.
"""
import argparse, contextlib
from typing import AsyncIterator, Optional

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from mcp_server.auth import MCPAuthError, principal_from_token
from mcp_server.server import build_server, request_principal, SERVER_NAME
from logger import get_logger


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8090
MCP_PATH = "/mcp"
_LOGGER = get_logger(name = "mcp_server", level = "INFO")


def _bearer_from_scope(scope: dict) -> Optional[str]:
    # ASGI headers are a list of (name, value) byte tuples; header names are lowercased.
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            decoded = value.decode("latin-1")
            scheme, _, token = decoded.partition(" ")
            if scheme.lower() == "bearer" and token:
                return token.strip()
            return None
    return None


async def _send_401(send, detail: str) -> None:
    import json
    body = json.dumps({"error": "unauthorized", "detail": detail}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"www-authenticate", b"Bearer"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": body})


def build_http_app() -> Starlette:
    """
    Build the streamable-HTTP ASGI app: one shared MCP server behind a per-request
    auth step. Returned as a Starlette app so both `_run_http` (prod, via uvicorn)
    and the conformance test drive the exact same wiring.
    """
    server = build_server()
    session_manager = StreamableHTTPSessionManager(app = server, json_response = False)

    async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        # Per-request auth: verify the bearer token, publish the principal for this request's
        # scope, then delegate. Absent token -> anonymous (identity/medical still work, the
        # Core refuses search_user_documents). Present-but-invalid -> 401 before the manager.
        try:
            principal = principal_from_token(_bearer_from_scope(scope))
        except MCPAuthError as exc:
            await _send_401(send, str(exc))
            return

        with request_principal(principal):
            await session_manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    return Starlette(routes = [Mount(MCP_PATH, app = handle_mcp)], lifespan = lifespan)


def _run_http(host: str, port: int) -> None:
    import uvicorn

    _LOGGER.info(
        "%s serving streamable-HTTP at http://%s:%d%s", SERVER_NAME, host, port, MCP_PATH,
    )
    uvicorn.run(build_http_app(), host = host, port = port)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog = "mcp_server", description = "DocuMedAI MCP server")
    parser.add_argument("--host", default = DEFAULT_HOST, help = "HTTP bind host")
    parser.add_argument("--port", type = int, default = DEFAULT_PORT, help = "HTTP bind port")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _run_http(args.host, args.port)


if __name__ == "__main__":
    main()
