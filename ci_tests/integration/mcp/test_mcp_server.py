"""
MCP protocol conformance tests — over the real streamable-HTTP transport.

Starts the production ASGI app (`build_http_app()`) on a live uvicorn server bound to a
loopback port, then drives the actual JSON-RPC lifecycle with the official MCP SDK client.
This exercises the whole transport: client -> HTTP -> uvicorn -> Starlette -> auth ->
session manager -> Core -> back.

Lifecycle & discovery:
    - initialize -> advertises serverInfo + a non-null `tools` capability,
    - tools/list -> the 3 tools, each with a valid object inputSchema that matches the
      Core's list_tools() verbatim (the protocol advertises the single source of truth,
      not a re-derived schema).

Happy path:
    - tools/call identity (no auth) -> text content, not an error.

Error taxonomy — three distinct channels, per the MCP spec:
    - *Tool* errors are `CallToolResult.isError`, NOT JSON-RPC errors. Per spec the SDK
      catches every handler exception and returns an error *result* (the numeric code our
      adapter raises is not surfaced to the client), so these are distinguished by their
      message, not a code: an unknown tool (names the tool), schema-violating args (names
      the validation failure — rejected by the transport's inputSchema before the Core),
      and a missing principal (the refusal test above).
    - *Protocol* errors ARE JSON-RPC errors with a real numeric code, tested one code per
      case: PARSE_ERROR (-32700, unparseable bytes) and INVALID_REQUEST (-32600, a valid
      method call with no session). METHOD_NOT_FOUND (-32601) is not client-reachable (a
      session-less POST is rejected as -32600 before method routing) — its test is skipped
      with that reason rather than faked.
    - Auth failure is the third channel: a present-but-invalid bearer is rejected with HTTP
      401 before any JSON-RPC runs; a valid bearer is accepted.

Every assertion is a protocol-/auth-boundary check that takes no live LLM/RAG call (a
*successful* search_user_documents would drive the RAG paraphrase → LLM proxy, which has
no keys in CI; that "valid token returns the user's own chunks" criterion is covered by
the isolation tests in Issue 3.1 and the cross-surface equivalence test in Issue 3.3).
"""
import os, uuid, httpx, jwt, pytest

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client, streamablehttp_client
from mcp.types import INVALID_REQUEST, METHOD_NOT_FOUND, PARSE_ERROR

from mcp_server.server import SERVER_NAME
from toolcore.core import get_mcp_client

from ci_tests.integration.mcp.conftest import url as _url


pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.mcp]

EXPECTED_TOOLS = {"identity", "search_medical_knowledge", "search_user_documents"}
_JSONRPC_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _first_jsonrpc_object(body: str) -> dict:
    """
    Parse the first JSON-RPC object out of a streamable-HTTP response body. The transport
    may return plain JSON or frame the reply as SSE (`data: {...}` lines), so handle both.
    """
    import json

    # MCP streamable HTTP
    text = body.strip()

    if not text:
        return {}

    # Plain JSON format
    if text.startswith("{"):
        return json.loads(text)
    
    # SSE stream format
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise AssertionError(f"no JSON-RPC object found in response body: {body!r}")


async def test_initialize_and_tools_list_match_core(http_server):
    # Lifecycle handshake + discovery in one round trip: the server identifies itself and
    # advertises the `tools` capability (what makes tools/list and tools/call valid), then
    # tools/list must expose the Core's list_tools() verbatim — same names, same schemas
    # (the single source of truth), not a schema the transport layer re-derived.
    core_specs = {spec.name: spec for spec in get_mcp_client().list_tools()}
    async with streamable_http_client(_url(http_server)) as (read, write, _):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert init.serverInfo.name == SERVER_NAME
            assert init.capabilities.tools is not None
            listed = await session.list_tools()

    assert {t.name for t in listed.tools} == EXPECTED_TOOLS == set(core_specs)
    for t in listed.tools:
        spec = core_specs[t.name]
        assert t.description == spec.description
        assert t.inputSchema == spec.input_schema


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


async def test_unknown_tool_is_error_result(http_server):
    # Unknown tool: the Core raises ToolInputError. Per the MCP spec the SDK returns tool
    # failures as CallToolResult.isError (not a JSON-RPC error), carrying the tool name so
    # the caller can tell WHY it failed.
    async with streamable_http_client(_url(http_server)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("no_such_tool", {})
    assert res.isError
    text = "".join(c.text for c in res.content if c.type == "text")
    assert "no_such_tool" in text


async def test_bad_args_is_error_result(http_server):
    # Schema-violating args (query must be a string): rejected by the transport's own
    # inputSchema validation before the tool runs — so no RAG/LLM call happens — and
    # surfaced as an isError result flagged as a validation error (distinct from an
    # unknown tool: this failure names the schema violation, not the tool).
    async with streamable_http_client(_url(http_server)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("search_medical_knowledge", {"query": 123})
    assert res.isError
    text = "".join(c.text for c in res.content if c.type == "text")
    assert "validation" in text.lower()


async def _post_error_code(url: str, *, content=None, json=None) -> int:
    """POST a raw (unauthenticated) request and return the JSON-RPC error code it yields."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.post(url, headers=_JSONRPC_HEADERS, content=content, json=json)
    payload = _first_jsonrpc_object(resp.text)
    assert "error" in payload, f"expected a JSON-RPC error, got {payload}"
    return payload["error"]["code"]


async def test_parse_error_code(http_server):
    # Bytes that aren't valid JSON -> the transport can't parse the envelope at all ->
    # PARSE_ERROR. A real numeric JSON-RPC code, not an isError tool result.
    code = await _post_error_code(_url(http_server), content=b"{not valid json")
    assert code == PARSE_ERROR


async def test_invalid_request_error_code(http_server):
    # A well-formed JSON-RPC method call with no established session: the session manager
    # rejects the envelope ("Missing session ID") -> INVALID_REQUEST. The reachable
    # request-layer rejection distinct from PARSE_ERROR above.
    code = await _post_error_code(
        _url(http_server),
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert code == INVALID_REQUEST


@pytest.mark.skip(
    reason="METHOD_NOT_FOUND (-32601) is not client-reachable: a session-less POST is "
    "rejected as INVALID_REQUEST (-32600) before method routing, and the SDK client only "
    "sends known methods over an established session. Documented, not faked."
)
async def test_method_not_found_error_code(http_server):
    code = await _post_error_code(
        _url(http_server),
        json={"jsonrpc": "2.0", "id": 1, "method": "no/such/method", "params": {}},
    )
    assert code == METHOD_NOT_FOUND


async def test_valid_token_is_accepted(http_server):
    # Auth success side (mirror of the garbage-bearer 401 test): a valid bearer passes the
    # per-request auth step, so the lifecycle runs. A locally-signed HS256 token is enough
    # to exercise acceptance — no login round-trip or seeded user needed.
    token = jwt.encode(
        {"type": "local", "id": f"mcp-conf-{uuid.uuid4().hex[:8]}"},
        os.environ["AUTH_JWT_SECRET"],
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    async with streamablehttp_client(_url(http_server), headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
    assert listed.tools
