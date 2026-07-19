"""
Issue 1.1 contract tests — the load-bearing invariants of the Tool Core boundary:
1. every tool's advertised JSON Schema is itself valid (Phase 2 tools/list depends on this),
2. bad args raise the typed ToolInputError (the boundary contract),
3. the `identity` no-args branch executes without raising.

Pure schema/validation checks — no live services. Marked `integration` only for
CI-directory consistency.
"""
import jsonschema, pytest

from toolcore.contracts import ToolInputError
from toolcore.core import _BUILTIN_MCP_TOOLS, get_mcp_client


pytestmark = pytest.mark.integration


def test_every_tool_advertises_a_valid_json_schema():
    # Pydantic can emit a schema jsonschema rejects (draft/custom-type mismatch);
    # Phase 2's tools/list advertises input_schema verbatim, so it must be valid.
    for spec in _BUILTIN_MCP_TOOLS:
        jsonschema.Draft202012Validator.check_schema(spec.input_schema)
        assert spec.input_schema.get("type") == "object"


def test_invalid_args_raise_tool_input_error():
    # The boundary contract: malformed args surface as the typed ToolInputError.
    # message=None -> {"query": None} -> fails `query: str`.
    client = get_mcp_client()
    with pytest.raises(ToolInputError):
        client.execute_tool_call("search_medical_knowledge", message=None)  # type: ignore[arg-type]


def test_identity_accepts_empty_args():
    # Guards the IdentityArgs no-fields branch in execute_tool_call.
    client = get_mcp_client()
    out = client.execute_tool_call("identity", message="")
    assert out.startswith("[identity]:")
