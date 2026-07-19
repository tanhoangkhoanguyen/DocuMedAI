"""
MCP Tool Core contracts — the transport-agnostic type layer shared by the internal
agent (in-process) and the external MCP server (Phase 2).

Lives in backend/mcp so it depends on nothing under services/. The chatbot graph and
any MCP surface both import these types from here.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal

from pydantic import BaseModel


# ==================== Tool Contracts ====================
class ToolInputError(Exception):
    """Raised when tool call args fail JSON Schema / model validation at the Core boundary."""


class PrincipalRequiredError(ToolInputError):
    """
    Raised when a principal-scoped tool (requires_principal=True) is called without a
    principal. Subclasses ToolInputError so existing `except ToolInputError` sites still
    catch it — the Core boundary stays one error family.
    """


@dataclass(frozen = True)
class Principal:
    """
    Typed caller identity threaded per-call (NOT bound to a cached singleton). `source`
    distinguishes the internal LangGraph agent from the external MCP server (Phase 2).
    """
    user_id: str
    source: Literal["internal", "mcp"]


@dataclass(frozen = True)
class ToolResult:
    """
    Structured tool output. `content` holds content blocks (today only text; the shape
    leaves room for richer MCP content types in Phase 2). `duration_ms` is timing metadata
    for Phase 4 metrics.
    """
    tool_name: str
    content: List[Dict[str, Any]] = field(default_factory = list)
    duration_ms: float = 0.0

    def text(self) -> str:
        """Join text blocks with newlines (empty content -> "") — reproduces the legacy string."""
        return "\n".join(
            block["text"] for block in self.content if block.get("type") == "text"
        )


class RuntimeConfig(BaseModel):
    """
    Infra-level config, bound once at Core/supporter construction (NOT a per-call arg).
    Supporters key their singleton caches on these six fields.
    """
    chat_model: str
    temperature: float
    embedding_model: str
    embedding_dimension: int
    reranking_model: str
    reranking_threshold: float


# --- Per-tool arg models: the single source of truth for each tool's JSON Schema. ---
class SearchMedicalKnowledgeArgs(BaseModel):
    query: str


class SearchUserDocumentsArgs(BaseModel):
    # Principal-scoped: the user_id is supplied by the caller/principal, never by the arg.
    query: str


class IdentityArgs(BaseModel):
    # No fields => accepts empty args.
    pass


# handler signature: (args_model, RuntimeConfig, principal_user_id) -> str
ToolHandler = Callable[[BaseModel, RuntimeConfig, str], str]


@dataclass(frozen = True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_schema: Dict[str, Any]        # ArgModel.model_json_schema()
    args_model: type[BaseModel]         # used to construct the validated args object
    handler: ToolHandler
    requires_principal: bool = False
