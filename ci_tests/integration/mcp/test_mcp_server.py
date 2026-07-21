"""
MCP server end-to-end tests — over the real streamable-HTTP transport.

Starts the production ASGI app (`build_http_app()`) on a live uvicorn server bound to a
loopback port, then drives the actual JSON-RPC lifecycle with the official MCP SDK client:

    1. tools/list -> the 3 tools, each with a valid object inputSchema,
    2. tools/call identity (no auth) -> text content, not an error,
    3. tools/call search_user_documents (no auth) -> refused (isError): the Core's
       principal gate reaches the protocol boundary,
    4. a garbage bearer -> HTTP 401 before any tool runs.

This exercises the whole transport: client -> HTTP -> uvicorn -> Starlette -> auth ->
session manager -> Core -> back. Every assertion is a protocol-/auth-boundary check that
takes no live LLM/RAG call (a *successful* search_user_documents would drive the RAG
paraphrase → LLM proxy, which has no keys in CI; that "valid token returns the user's own
chunks" criterion is covered by the seeded-Qdrant isolation tests in Issue 3.1/3.3).
"""
import httpx, pytest, socket, threading, time, uvicorn

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_server.__main__ import build_http_app, MCP_PATH


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

EXPECTED_TOOLS = {"identity", "search_medical_knowledge", "search_user_documents"}


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


def _url(base: str) -> str:
    # Trailing slash: Starlette's Mount serves the app at "/mcp/" and 307-redirects
    # "/mcp" -> "/mcp/". Hitting the canonical path avoids a redirect the SDK's custom
    # httpx client won't follow by default.
    return f"{base}{MCP_PATH}/"


async def test_tools_list_returns_three_tools_with_valid_schemas(http_server):
    async with streamable_http_client(_url(http_server)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {t.name for t in listed.tools}
            assert names == EXPECTED_TOOLS
            for t in listed.tools:
                assert isinstance(t.inputSchema, dict)
                assert t.inputSchema.get("type") == "object"


async def test_tools_call_identity_returns_text(http_server):
    async with streamable_http_client(_url(http_server)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("identity", {})
            assert not res.isError, res.content
            text = "".join(c.text for c in res.content if c.type == "text")
            assert "DocuMedAI" in text


async def test_user_documents_without_principal_is_refused(http_server):
    # No Authorization header -> anonymous -> the principal-scoped tool must surface a
    # JSON-RPC error (not a 200 wrapping an error string).
    async with streamable_http_client(_url(http_server)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            refused = await session.call_tool("search_user_documents", {"query": "x"})
            assert refused.isError
            text = "".join(c.text for c in refused.content if c.type == "text")
            assert "principal" in text.lower()


async def test_garbage_bearer_is_rejected_with_401(http_server):
    # A present-but-invalid token is rejected at the auth step, before any tool runs.
    # follow_redirects: Starlette's Mount 307-redirects /mcp -> /mcp/; the auth check
    # runs on the redirected request.
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.post(
            _url(http_server),
            headers={
                "Authorization": "Bearer not.a.jwt",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
    assert resp.status_code == 401
