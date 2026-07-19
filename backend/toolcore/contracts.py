"""
MCP Tool Core contracts — the transport-agnostic type layer shared by the internal
agent (in-process) and the external MCP server (Phase 2).

Lives in backend/mcp so it depends on nothing under services/. The chatbot graph and
any MCP surface both import these types from here.
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict

from pydantic import BaseModel


# ==================== Tool Contracts ====================
class ToolInputError(Exception):
    """Raised when tool call args fail JSON Schema / model validation at the Core boundary."""


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
