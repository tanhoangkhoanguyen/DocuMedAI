"""
Shared harness for the MCP integration tests: a loopback uvicorn running the production
ASGI app, so every test in this package drives the real transport (client -> HTTP ->
uvicorn -> Starlette -> auth -> session manager -> Core) instead of an in-memory shortcut.

The server runs in-process on a background thread, so a monkeypatch on the Tool Core's
singletons reaches the tools it serves.
"""
import socket
import threading
import time

import pytest
import uvicorn

from mcp_server.__main__ import build_http_app, MCP_PATH


def _free_port() -> int:
    """Find an available port to run the test server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def http_server():
    """Run the prod ASGI app on a loopback uvicorn in a background thread."""
    port = _free_port()
    config = uvicorn.Config(
        build_http_app(), host="127.0.0.1", port=port, log_level="warning",
    )
    server = uvicorn.Server(config)
    # (background thread) Uvicorn.Server.run() is blocking, but we want to
    # yield the URL to the test coroutine while the server is running.
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait until the port accepts connections (server.started flips once the loop is up).
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("uvicorn did not start in time")

    yield f"http://127.0.0.1:{port}"

    # Shut down the server after the test and wait for the thread to exit.
    server.should_exit = True
    thread.join(timeout=10)


def url(base: str) -> str:
    # Trailing slash: Starlette's Mount serves the app at "/mcp/" and 307-redirects
    # "/mcp" -> "/mcp/". Hitting the canonical path avoids a redirect the SDK's custom
    # httpx client won't follow by default.
    return f"{base}{MCP_PATH}/"
