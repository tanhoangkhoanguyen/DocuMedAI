"""
Tool Core contract tests — the load-bearing invariants of the boundary. Every test drives
the primary call_tool surface; execute_tool_call is a thin shim over it (covered
transitively by test_call_tool_returns_tool_result's .text() equality assertion).

1. every tool's advertised JSON Schema is itself valid (Phase 2 tools/list depends on this),
2. call_tool refuses a principal-scoped tool with no principal (isolation, no leak),
3. call_tool returns a ToolResult whose .text() matches the legacy shim string,
4. the requires_principal gate clears with a principal (bad args -> ToolInputError proves it),
5. list_tools exposes the structured catalog.

All pure schema/validation checks — no live LLM/RAG call. Marked `integration` for
CI-directory consistency.
"""
import jsonschema, pytest

from toolcore.contracts import Principal, PrincipalRequiredError, ToolInputError, ToolResult, ToolSpec
from toolcore.core import _BUILTIN_MCP_TOOLS, get_mcp_client


pytestmark = pytest.mark.integration


def test_every_tool_advertises_a_valid_json_schema():
    # Pydantic can emit a schema jsonschema rejects (draft/custom-type mismatch);
    # Phase 2's tools/list advertises input_schema verbatim, so it must be valid.
    for spec in _BUILTIN_MCP_TOOLS:
        jsonschema.Draft202012Validator.check_schema(spec.input_schema)
        assert spec.input_schema.get("type") == "object"


def test_call_tool_requires_principal_refuses():
    # AC: a principal-scoped tool called with principal=None refuses (no leak) —
    # the Core gates on requires_principal BEFORE touching the supporter.
    client = get_mcp_client()
    with pytest.raises(PrincipalRequiredError):
        client.call_tool("search_user_documents", {"query": "x"}, principal=None)


def test_execute_tool_call_degrades_when_principal_missing():
    # The internal shim's counterpart to the refusal test: call_tool RAISES so the future
    # MCP surface gets a typed error, but the legacy shim (what nodes.py calls) catches it,
    # logs a warning, and degrades to "" so a mis-wired unauthenticated graph path yields no
    # user-doc passages instead of crashing the task or leaking another tenant's data.
    client = get_mcp_client()
    assert client.execute_tool_call("search_user_documents", "x", user_id="") == ""


def test_call_tool_returns_tool_result():
    # AC: call_tool returns a structured ToolResult whose .text() equals the string the
    # legacy execute_tool_call path produces. Uses `identity` (handler makes no external
    # LLM/Qdrant call) so this stays a pure boundary-contract check, not a live-RAG test.
    client = get_mcp_client()
    result = client.call_tool("identity", {}, principal=Principal("u1", "internal"))
    assert isinstance(result, ToolResult)
    assert result.tool_name == "identity"
    assert result.text() == client.execute_tool_call("identity", message="")


def test_call_tool_accepts_principal_for_scoped_tool():
    # AC complement to the refusal test: the SAME principal-scoped tool that refuses with
    # principal=None passes the Core's requires_principal gate WITH a principal (it then
    # proceeds to validate/execute — we only assert the gate doesn't refuse here). Uses a
    # bad-args payload so validation short-circuits before any live RAG call: a
    # ToolInputError (NOT PrincipalRequiredError) proves the gate was cleared.
    client = get_mcp_client()
    with pytest.raises(ToolInputError) as exc:
        client.call_tool(
            "search_user_documents", {"query": None}, principal=Principal("u1", "internal"))
    assert not isinstance(exc.value, PrincipalRequiredError)


def test_list_tools_returns_specs():
    # list_tools exposes the structured catalog (Phase 2 tools/list source).
    client = get_mcp_client()
    tools = client.list_tools()
    assert all(isinstance(t, ToolSpec) for t in tools)
