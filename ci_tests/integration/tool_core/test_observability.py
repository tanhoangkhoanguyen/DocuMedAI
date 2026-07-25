"""
Observability tests for the Tool Core: every call_tool invocation — success or failure —
produces one timed record (Prometheus counter + histogram) tagged by tool/surface/outcome,
plus one JSON log line. Drives call_tool directly (the wrapper lives in the Core, no HTTP
needed) using `identity` for the success path so no live LLM/RAG is touched.

Prometheus metrics are process-global and cumulative, so every assertion reads a DELTA
around the call, never an absolute value.
"""
import orjson
import pytest

from toolcore.contracts import (
    Principal,
    PrincipalRequiredError,
    ToolInputError,
    ToolNotFoundError,
)
from toolcore.core import get_mcp_client
from toolcore.observability import TOOL_CALLS_TOTAL, classify_outcome


pytestmark = pytest.mark.integration


def _count(tool: str, surface: str, outcome: str) -> float:
    # Cumulative counter value for one label set (0.0 before it is first observed).
    return TOOL_CALLS_TOTAL.labels(tool=tool, surface=surface, outcome=outcome)._value.get()


def test_success_records_success_outcome_and_internal_surface():
    client = get_mcp_client()
    before = _count("identity", "internal", "success")
    client.call_tool("identity", {}, principal=Principal("u1", "internal"))
    assert _count("identity", "internal", "success") == before + 1


def test_surface_is_derived_from_principal_source():
    # The SAME tool tagged mcp when the principal says so — proves surface comes from the
    # principal, not a fixed default.
    client = get_mcp_client()
    before = _count("identity", "mcp", "success")
    client.call_tool("identity", {}, principal=Principal("u1", "mcp"))
    assert _count("identity", "mcp", "success") == before + 1


def test_no_principal_is_recorded_as_internal():
    client = get_mcp_client()
    before = _count("identity", "internal", "success")
    client.call_tool("identity", {}, principal=None)
    assert _count("identity", "internal", "success") == before + 1


def test_unknown_tool_records_not_found():
    client = get_mcp_client()
    before = _count("no_such_tool", "internal", "not_found")
    with pytest.raises(ToolNotFoundError):
        client.call_tool("no_such_tool", {}, principal=Principal("u1", "internal"))
    assert _count("no_such_tool", "internal", "not_found") == before + 1


def test_bad_args_records_input_error():
    client = get_mcp_client()
    before = _count("search_medical_knowledge", "internal", "input_error")
    with pytest.raises(ToolInputError):
        client.call_tool(
            "search_medical_knowledge", {"query": None}, principal=Principal("u1", "internal"))
    assert _count("search_medical_knowledge", "internal", "input_error") == before + 1


def test_missing_principal_records_auth_error():
    # search_user_documents requires_principal — called with principal=None it refuses at
    # the gate, and that must be tagged auth_error (NOT input_error), even though
    # PrincipalRequiredError subclasses ToolInputError.
    client = get_mcp_client()
    before = _count("search_user_documents", "internal", "auth_error")
    with pytest.raises(PrincipalRequiredError):
        client.call_tool("search_user_documents", {"query": "x"}, principal=None)
    assert _count("search_user_documents", "internal", "auth_error") == before + 1


def test_handler_failure_records_upstream_error(monkeypatch):
    # A handler that blows up past validation (RAG/LLM/supporter fault) records
    # upstream_error and re-raises unchanged. Patch the supporter factory the
    # search_medical_knowledge handler calls so no live retrieval happens.
    def _boom(*_a, **_k):
        raise RuntimeError("supporter down")

    monkeypatch.setattr("toolcore.core.get_medical_supporter", _boom)
    client = get_mcp_client()
    before = _count("search_medical_knowledge", "internal", "upstream_error")
    with pytest.raises(RuntimeError):
        client.call_tool(
            "search_medical_knowledge", {"query": "x"}, principal=Principal("u1", "internal"))
    assert _count("search_medical_knowledge", "internal", "upstream_error") == before + 1


def test_classify_outcome_orders_subclass_first():
    # Subclasses must win over their ToolInputError base; success is None.
    assert classify_outcome(None) == "success"
    assert classify_outcome(ToolNotFoundError("x")) == "not_found"
    assert classify_outcome(PrincipalRequiredError("x")) == "auth_error"
    assert classify_outcome(ToolInputError("x")) == "input_error"
    assert classify_outcome(RuntimeError("x")) == "upstream_error"


def test_emits_one_json_log_line(caplog):
    client = get_mcp_client()
    with caplog.at_level("INFO", logger="toolcore"):
        client.call_tool("identity", {}, principal=Principal("u1", "internal"))

    records = [
        orjson.loads(r.message) for r in caplog.records
        if r.name == "toolcore" and r.message.startswith("{")
    ]
    hits = [
        r for r in records
        if r.get("event") == "tool_call" and r["tool"] == "identity" and r["surface"] == "internal"
    ]
    assert hits, "no tool_call JSON log line emitted"
    line = hits[-1]
    assert line["outcome"] == "success"
    assert isinstance(line["duration_ms"], (int, float))
