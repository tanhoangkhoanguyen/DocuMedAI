"""
Cross-surface equivalence — the same Tool Core, called two ways, returns the same content.

The system's core claim is "one execution core, two surfaces": the tools are invoked both
in-process (`get_mcp_client().call_tool(...)` -> `ToolResult`) and over the MCP protocol
(JSON-RPC `tools/call` over streamable-HTTP -> `CallToolResult`). This module proves the two
surfaces return identical content for the same input + principal, so the ONLY difference
between them is the transport envelope.

Both surfaces resolve to the same cached core singleton (the MCP server's build_server()
calls the same get_mcp_client()), and the loopback server runs in-process, so a single
monkeypatch on the supporter singletons reaches both.

The RAG search tools paraphrase/generalize with the LLM (Vertex AI) and embed before
retrieving — none of which CI can run. We stub those leaves so `retrieve_query` returns a
FIXED sentinel passage; the tool then returns a deterministic string on both surfaces. This
is intentionally NOT a live-Qdrant test: the live vector path and the user_id filter are
proven by test_qdrant_client.py, and the principal->filter wiring by the tool_core isolation
tests. Here the sole variable under test is internal-vs-MCP transport equivalence.
"""
import os
import uuid

import jwt
import pytest

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from toolcore.contracts import Principal, RuntimeConfig
from toolcore.core import get_mcp_client
from toolcore.tools.medical_supporter import get_medical_supporter
from toolcore.tools.user_document_supporter import get_user_document_supporter

from ci_tests.fixtures.vector_db import EMBEDDED_QUERY
from ci_tests.integration.mcp.conftest import url as _url


pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.mcp]


# The config get_mcp_client() builds from its defaults (core.py). The handlers resolve
# get_*_supporter(config) with this exact config, so patching the config-keyed singletons
# patches the instances the calls will use — on both surfaces.
_DEFAULT_CONFIG = RuntimeConfig(
    chat_model="gemini-2.5-flash",
    temperature=0,
    embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    embedding_dimension=384,
    reranking_model="BAAI/bge-reranker-v2-m3",
    reranking_threshold=-5,
)

# Fixed passage the stubbed retrieval returns, so the expected tool output is deterministic
# and identical across surfaces.
_SENTINEL = "sentinel-passage-for-equivalence"
_MATCHED_USER = "equiv-user-A"


class _Point:
    """Minimal stand-in for a Qdrant scored point carrying a text payload."""
    def __init__(self, text):
        self.payload = {"text": text}


class _Resp:
    """Minimal stand-in for a Qdrant QueryResponse (has .points)."""
    def __init__(self, points):
        self.points = points


def _stub_supporter_leaves(monkeypatch, supporter, private_prefix):
    """
    Neutralize the LLM/embed/rerank leaves on one supporter singleton and make retrieval
    return the fixed sentinel passage. Only the transport differs between the two surfaces,
    so this one patch (shared singleton) makes both deterministic. monkeypatch auto-reverts.
    """
    rag = getattr(supporter, f"_{private_prefix}__rag_client")
    qdrant = getattr(supporter, f"_{private_prefix}__qdrant_client")

    monkeypatch.setattr(rag, "paraphrase_message", lambda message, number=1: ["q"])
    monkeypatch.setattr(rag, "generalize_message", lambda message: "")
    monkeypatch.setattr(
        rag, "rerank_queries", lambda queries, original_query, top_k: queries[:top_k])
    monkeypatch.setattr(qdrant, "embed_query", lambda query: EMBEDDED_QUERY)
    monkeypatch.setattr(qdrant, "retrieve_query", lambda **kwargs: _Resp([_Point(_SENTINEL)]))


def _install_sentinel_supporters(monkeypatch):
    """Stub both RAG-backed supporters (medical + user-doc) to the fixed sentinel."""
    _stub_supporter_leaves(
        monkeypatch, get_medical_supporter(_DEFAULT_CONFIG), "MedicalSupporter")
    _stub_supporter_leaves(
        monkeypatch, get_user_document_supporter(_DEFAULT_CONFIG), "UserDocumentSupporter")


def _mcp_text(res) -> str:
    """Join the text blocks of an MCP CallToolResult (no ToolResult.text() on this side)."""
    return "".join(c.text for c in res.content if c.type == "text")


def _local_bearer(user_id: str) -> dict:
    """A valid local HS256 bearer whose `id` becomes Principal(user_id, source="mcp")."""
    token = jwt.encode(
        {"type": "local", "id": user_id},
        os.environ["AUTH_JWT_SECRET"],
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


async def _mcp_call(http_server, name, args, headers):
    async with streamablehttp_client(_url(http_server), headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(name, args)


async def test_identity_equivalent_across_surfaces(http_server):
    # No RAG, no principal — a pure static string. The cheapest possible proof that the two
    # transports carry identical content.
    internal = get_mcp_client().call_tool("identity", {}, principal=None).text()
    external = _mcp_text(await _mcp_call(http_server, "identity", {}, headers={}))

    assert internal == external
    assert internal.startswith("[identity]: ")


async def test_medical_knowledge_equivalent_across_surfaces(http_server, monkeypatch):
    # Principal-free RAG tool: stub the leaves to the sentinel, then both surfaces must
    # return the identical wrapped passage.
    _install_sentinel_supporters(monkeypatch)

    internal = get_mcp_client().call_tool(
        "search_medical_knowledge", {"query": "what is sepsis"}, principal=None).text()
    external = _mcp_text(await _mcp_call(
        http_server, "search_medical_knowledge", {"query": "what is sepsis"}, headers={}))

    assert internal == external
    assert internal == f"[search_medical_knowledge]: {_SENTINEL}"


async def test_user_documents_equivalent_across_surfaces(http_server, monkeypatch):
    # Principal-scoped tool with MATCHED principals: internal Principal(A,"internal") and the
    # MCP bearer id=A both resolve to the same user_id, so retrieval (stubbed) yields the same
    # sentinel and the two surfaces must agree.
    _install_sentinel_supporters(monkeypatch)

    internal = get_mcp_client().call_tool(
        "search_user_documents", {"query": "my lab results"},
        principal=Principal(_MATCHED_USER, "internal")).text()
    external = _mcp_text(await _mcp_call(
        http_server, "search_user_documents", {"query": "my lab results"},
        headers=_local_bearer(_MATCHED_USER)))

    assert internal == external
    assert internal == f"[search_user_documents]: {_SENTINEL}"
