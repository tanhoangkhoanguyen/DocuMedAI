"""
Entry point:  python -m mcp_server --transport {stdio,http}

  stdio  — for local MCP hosts (Claude Desktop, MCP Inspector via stdio). Speaks
           JSON-RPC over stdin/stdout; no network.
  http   — streamable-HTTP (the current MCP HTTP transport, not deprecated SSE),
           served by uvicorn under Starlette at the /mcp path.

Both transports run the SAME `build_server()` instance — one execution core, two
serving surfaces.
"""
import anyio, argparse, contextlib, json, logging, os, sys
from typing import AsyncIterator, Optional

from mcp_server.auth import MCPAuthError, principal_from_token
from mcp_server.server import build_server, request_principal, SERVER_NAME
from logger import get_logger


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8090
MCP_PATH = "/mcp"
_LOGGER = get_logger(name = "mcp_server", level = "INFO")


def _redirect_stdout_logging_to_stderr() -> None:
    # stdio transport uses stdout for the JSON-RPC stream — ANY log line on stdout
    # corrupts it (the client's JSON.parse chokes). The shared logger (logger.py)
    # attaches a StreamHandler(sys.stdout); repoint every root/named-logger stdout
    # handler to stderr so logs survive (stderr + file) without polluting the wire.
    for logger in (logging.getLogger(), _LOGGER):
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout:
                handler.setStream(sys.stderr)


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


def _run_stdio() -> None:
    from mcp.server.stdio import stdio_server

    _redirect_stdout_logging_to_stderr()

    # stdio has no HTTP headers: the token arrives via env var, read ONCE at process start
    # (one process = one client = one principal). An invalid token aborts launch here rather
    # than degrading to anonymous — a broken MCP_AUTH_TOKEN is a misconfiguration.
    principal = principal_from_token(os.getenv("MCP_AUTH_TOKEN"))
    if principal is not None:
        _LOGGER.info("stdio transport authenticated as user_id=%s", principal.user_id)
    server = build_server(principal = principal)

    async def arun() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options()
            )

    _LOGGER.info("%s starting on stdio transport", SERVER_NAME)
    anyio.run(arun)


def _run_http(host: str, port: int) -> None:
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.types import Receive, Scope, Send
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

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
            _LOGGER.info(
                "%s serving streamable-HTTP at http://%s:%d%s",
                SERVER_NAME, host, port, MCP_PATH,
            )
            yield

    app = Starlette(routes = [Mount(MCP_PATH, app = handle_mcp)], lifespan = lifespan)
    uvicorn.run(app, host = host, port = port)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog = "mcp_server", description = "DocuMedAI MCP server")
    parser.add_argument(
        "--transport", choices = ("stdio", "http"), default = "stdio",
        help = "stdio (default) or streamable-HTTP",
    )
    parser.add_argument("--host", default = DEFAULT_HOST, help = "HTTP bind host")
    parser.add_argument("--port", type = int, default = DEFAULT_PORT, help = "HTTP bind port")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.transport == "stdio":
        _run_stdio()
    else:
        _run_http(args.host, args.port)


if __name__ == "__main__":
    main()
