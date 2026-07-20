"""
Entry point:  python -m mcp_server --transport {stdio,http}

  stdio  — for local MCP hosts (Claude Desktop, MCP Inspector via stdio). Speaks
           JSON-RPC over stdin/stdout; no network.
  http   — streamable-HTTP (the current MCP HTTP transport, not deprecated SSE),
           served by uvicorn under Starlette at the /mcp path.

Both transports run the SAME `build_server()` instance — one execution core, two
serving surfaces.
"""
import anyio, argparse, contextlib, logging, sys
from typing import AsyncIterator

from mcp_server.server import build_server, SERVER_NAME
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


def _run_stdio() -> None:
    from mcp.server.stdio import stdio_server

    _redirect_stdout_logging_to_stderr()
    server = build_server()

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
