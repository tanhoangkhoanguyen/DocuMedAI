"""
Tool Core principal-isolation tests — prove a Principal's user_id is what scopes
user-document retrieval, and that a user-less principal can never reach an unfiltered read.

The schema-validity / bad-args / requires-principal-refusal contract checks live alongside
this file in test_tool_contracts.py; this module deliberately covers only the
isolation half of the same issue, and the Qdrant user_id filter *mechanism* is covered by
test_qdrant_user_isolation in ci_tests/integration/vector_db/test_qdrant_client.py. What is
proven *here* is the wiring in between: principal.user_id -> call_tool -> the handler ->
retrieve_query(user_id=...).

The real search_user_documents path paraphrases/generalizes the query with the LLM and
reranks with a local cross-encoder before retrieving — none of which CI can run. We stub
those leaves on the supporter singleton and spy on the one call that carries the isolation
guarantee (retrieve_query), so the real Core dispatch, the requires_principal gate, and the
empty-user short-circuit all still execute unmocked.
"""
import pytest

from ci_tests.fixtures.vector_db import EMBEDDED_QUERY
from toolcore.contracts import Principal, RuntimeConfig, ToolResult
from toolcore.core import get_mcp_client
from toolcore.tools.user_document_supporter import get_user_document_supporter


pytestmark = pytest.mark.integration


# The six-value config get_mcp_client() builds from its defaults (core.py). The handler
# resolves get_user_document_supporter(config) with this exact config, so patching the
# supporter keyed on it patches the instance the call will use.
_DEFAULT_CONFIG = RuntimeConfig(
    chat_model="gemini-2.5-flash",
    temperature=0,
    embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    embedding_dimension=384,
    reranking_model="BAAI/bge-reranker-v2-m3",
    reranking_threshold=-5,
)


class _Point:
    """Minimal stand-in for a Qdrant scored point carrying a text payload."""
    def __init__(self, text):
        self.payload = {"text": text}


class _Resp:
    """Minimal stand-in for a Qdrant QueryResponse (has .points)."""
    def __init__(self, points):
        self.points = points


def _install_stub_supporter(monkeypatch, retrieve_spy):
    """
    Neutralize the LLM/embedding/rerank leaves on the config-keyed supporter singleton and
    route retrieval through `retrieve_spy`. Everything the isolation guarantee flows through
    (call_tool, the gate, the handler, the user_id short-circuit) stays real.
    monkeypatch auto-reverts, so the next test gets the unpatched singleton back.
    """
    supporter = get_user_document_supporter(_DEFAULT_CONFIG)
    rag = supporter._UserDocumentSupporter__rag_client
    qdrant = supporter._UserDocumentSupporter__qdrant_client

    # LLM query expansion -> deterministic, no network. One seed is enough.
    monkeypatch.setattr(rag, "paraphrase_message", lambda message, number=1: ["q"])
    monkeypatch.setattr(rag, "generalize_message", lambda message: "")  # skipped by the guard
    # Local cross-encoder -> identity passthrough, no model inference.
    monkeypatch.setattr(
        rag, "rerank_queries", lambda queries, original_query, top_k: queries[:top_k])
    # Embedding -> the fixed 384-dim vector, no HF inference.
    monkeypatch.setattr(qdrant, "embed_query", lambda query: EMBEDDED_QUERY)
    # The one call under test.
    monkeypatch.setattr(qdrant, "retrieve_query", retrieve_spy)


def test_principal_user_id_reaches_qdrant_filter(monkeypatch):
    # AC: the Principal's identity — not an ambient default — is what scopes retrieval, and
    # two principals produce two distinct user_id filters (no cross-tenant bleed via the Core).
    seen_user_ids = []

    def retrieve_spy(**kwargs):
        seen_user_ids.append(kwargs.get("user_id"))
        return _Resp([_Point(f"doc for {kwargs.get('user_id')}")])

    _install_stub_supporter(monkeypatch, retrieve_spy)
    client = get_mcp_client()

    result_a = client.call_tool(
        "search_user_documents", {"query": "x"}, principal=Principal("user-A", "mcp"))
    assert isinstance(result_a, ToolResult)
    assert seen_user_ids == ["user-A"], f"expected retrieval scoped to user-A, got {seen_user_ids}"

    seen_user_ids.clear()
    client.call_tool(
        "search_user_documents", {"query": "x"}, principal=Principal("user-B", "mcp"))
    assert seen_user_ids == ["user-B"], f"expected retrieval scoped to user-B, got {seen_user_ids}"


def test_empty_user_principal_never_queries_unfiltered(monkeypatch):
    # Fail-open guard: retrieve_query drops the filter when user_id is falsy
    # (qdrant_client.py: `... if user_id else None`), returning EVERY user's docs. A
    # Principal("", "mcp") clears the requires_principal gate (which only checks
    # `principal is None`), so the empty-user short-circuit in the supporter is the only
    # thing preventing an unfiltered read. Prove retrieve_query is never reached.
    called = False

    def retrieve_spy(**kwargs):
        nonlocal called
        called = True
        return _Resp([])

    _install_stub_supporter(monkeypatch, retrieve_spy)
    client = get_mcp_client()

    result = client.call_tool(
        "search_user_documents", {"query": "x"}, principal=Principal("", "mcp"))
    assert result.text() == "[search_user_documents]: "  # degraded to no passages
    assert not called, "empty-user principal reached retrieve_query — unfiltered-read leak path"
