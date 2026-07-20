"""
MCP server end-to-end tests — Issue 2.1's acceptance criteria, over the real protocol.

Spawns `python -m mcp_server --transport stdio` as a subprocess and drives the actual
JSON-RPC lifecycle with the official MCP SDK client, asserting:

    1. tools/list -> the 3 tools, each with a valid object inputSchema,
    2. tools/call identity -> text content, not an error,
    3. tools/call search_user_documents with no principal -> refused as a JSON-RPC
       error (isError), proving the Core's principal gate reaches the protocol boundary.

Unlike test_tool_contracts.py (which calls the Core in-process), this exercises the
whole transport: client -> stdio -> server -> Core -> back. `identity` takes no live
LLM/RAG call, so this stays a protocol-boundary check, not a live-RAG test.
"""
import os, sys, pytest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

EXPECTED_TOOLS = {"identity", "search_medical_knowledge", "search_user_documents"}


# Absolute path to backend/, derived from THIS file's location (not os.getcwd(), which
# depends on where pytest was launched): ci_tests/integration/mcp/ -> repo root -> backend/.
_BACKEND_DIR = Path(__file__).resolve().parents[3] / "backend"


def _server_params() -> StdioServerParameters:
    # The subprocess is a fresh interpreter — it does NOT inherit pytest.ini's
    # `pythonpath = backend .`, so put backend/ on PYTHONPATH explicitly for the child
    # so `python -m mcp_server` (and its `toolcore`/`logger` imports) resolve.
    child_env = dict(os.environ)
    child_env["PYTHONPATH"] = str(_BACKEND_DIR)
    return StdioServerParameters(
        command = sys.executable,
        args = ["-m", "mcp_server", "--transport", "stdio"],
        env = child_env,
    )


async def test_tools_list_returns_three_tools_with_valid_schemas():
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {t.name for t in listed.tools}
            assert names == EXPECTED_TOOLS
            for t in listed.tools:
                assert isinstance(t.inputSchema, dict)
                assert t.inputSchema.get("type") == "object"


async def test_tools_call_identity_returns_text():
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("identity", {})
            assert not res.isError, res.content
            text = "".join(c.text for c in res.content if c.type == "text")
            assert "DocuMedAI" in text


async def test_tools_call_user_documents_without_principal_is_refused():
    # Issue 2.1 has no auth surface yet -> principal=None. The principal-scoped tool
    # must surface a JSON-RPC error (not a 200 with an error string). Issue 2.2 wires
    # JWT-derived principals so a real user's call succeeds.
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            refused = await session.call_tool("search_user_documents", {"query": "x"})
            assert refused.isError
