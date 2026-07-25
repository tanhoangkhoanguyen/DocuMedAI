import time
from typing import Dict, List, Optional, Sequence

from pydantic import ValidationError

from toolcore.contracts import (
    IdentityArgs,
    Principal,
    PrincipalRequiredError,
    RuntimeConfig,
    SearchMedicalKnowledgeArgs,
    SearchUserDocumentsArgs,
    ToolInputError,
    ToolNotFoundError,
    ToolResult,
    ToolSpec,
)
from toolcore.observability import classify_outcome, record_tool_call
from toolcore.tools.medical_supporter import get_medical_supporter
from toolcore.tools.user_document_supporter import get_user_document_supporter
from logger import get_logger


_LOGGER = get_logger(name = "toolcore", level = "INFO")


def search_medical_knowledge(
        args: SearchMedicalKnowledgeArgs, config: RuntimeConfig, user_id: str = "") -> str:
    client = get_medical_supporter(config)
    return f"[search_medical_knowledge]: {client.run(args.query)}"


def search_user_documents(
        args: SearchUserDocumentsArgs, config: RuntimeConfig, user_id: str = "") -> str:
    client = get_user_document_supporter(config)
    return f"[search_user_documents]: {client.run(args.query, user_id)}"


_ABOUT_DOCUMEDAI = (
    "What I am: I am the DocuMedAI chatbot, an AI assistant for analyzing medical documents. "
    "Users upload medical documents and ask me questions about them through a chat interface.\n"
    "Why I exist / what this project is for: DocuMedAI is a full-stack AI-powered medical "
    "document analysis system. It helps users understand their medical documents by answering "
    "questions grounded in the uploaded content, using a multi-agent workflow with RAG "
    "(retrieval-augmented generation) over a vector database, persistent conversation memory, "
    "and caching for fast, context-aware responses.\n"
    "How I work (high level): each message is routed through a LangGraph workflow "
    "(topic checking, message analysis, long-term memory retrieval, multi-agent answering, "
    "and schema updating). Answers are retrieved from documents via Qdrant vector search with "
    "cross-encoder reranking.\n"
    "Who created me: My creator is tanhoangkhoanguyen (Khoa Nguyen), together with his "
    "collaborators.\n"
    "Where to learn more: You can find more information about the codebase at "
    "https://github.com/tanhoangkhoanguyen/DocuMedAI"
)

def identity(args: IdentityArgs, config: RuntimeConfig, user_id: str = "") -> str:
    return f"[identity]: {_ABOUT_DOCUMEDAI}"


_MCP_DICT: dict = {}
_BUILTIN_MCP_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name = "search_medical_knowledge",
        title = "Search Medical Knowledge",
        description = (
            "A tool that retrieve medical terms and provide insight for that term. "
            "Pass a short natural-language query (e.g. 'The cause for X' or 'Definition of X')."
        ),
        input_schema = SearchMedicalKnowledgeArgs.model_json_schema(),
        args_model = SearchMedicalKnowledgeArgs,
        handler = search_medical_knowledge,
    ),
    ToolSpec(
        name = "identity",
        title = "Identity",
        description = (
            "Provides general information about this chatbot and project. Use it to answer "
            "meta questions such as 'What are you?', 'Why do you exist / what is this project "
            "for?', 'Who created you?', or any question about DocuMedAI itself, its purpose, or "
            "its creator. Takes no meaningful input."
        ),
        input_schema = IdentityArgs.model_json_schema(),
        args_model = IdentityArgs,
        handler = identity,
    ),
    ToolSpec(
        name = "search_user_documents",
        title = "Search User Documents",
        description = (
            "Retrieve passages from the CURRENT USER's own uploaded documents (their PDFs, "
            "DOCX, or text files) to answer questions grounded in those files. Use this whenever "
            "the user refers to 'my document', 'the file I uploaded', 'my report/record', or "
            "asks something that should be answered from their uploaded content. "
            "Pass a short natural-language query describing what to find."
        ),
        input_schema = SearchUserDocumentsArgs.model_json_schema(),
        args_model = SearchUserDocumentsArgs,
        handler = search_user_documents,
        requires_principal = True,
    ),
)


class MCPServer:
    """
    In-memory tool registry for the planner catalog and execution by tool name.
    """

    def __init__(
            self,
            chat_model: str,
            temperature: float,
            embedding_model: str,
            embedding_dimension: int,
            reranking_model: str,
            reranking_threshold: float,
            extra_tools: Optional[Sequence[ToolSpec]] = None
        ):
        self.__config = RuntimeConfig(
            chat_model = chat_model,
            temperature = temperature,
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension,
            reranking_model = reranking_model,
            reranking_threshold = reranking_threshold,
        )

        self.__registry: Dict[str, ToolSpec] = {}
        self._build_mcp_registry(extra_tools or ())

    def _build_mcp_registry(self, extra_tools: Sequence[ToolSpec]) -> None:
        for tool in (*_BUILTIN_MCP_TOOLS, *extra_tools):
            self.__registry[tool.name] = tool

    def format_registry(self) -> str:
        if not self.__registry:
            return "(no MCP tools configured)"
        lines = []
        for name in sorted(self.__registry.keys()):
            t = self.__registry[name]
            lines.append(f"- {t.name}: {t.description}")
        return "\n".join(lines)

    def has_tool(self, name: str) -> bool:
        return name in self.__registry

    def list_tools(self) -> List[ToolSpec]:
        # Structured catalog for the transport-agnostic surface (Phase 2 tools/list).
        # Sorted by name to match format_registry's ordering.
        return [self.__registry[name] for name in sorted(self.__registry.keys())]

    def call_tool(
            self, name: str, args: dict, principal: Optional[Principal]) -> ToolResult:
        """
        Transport-agnostic primary surface: raw dict args + typed Principal -> ToolResult.
        Both the internal agent and the future MCP server call through here, so per-user
        isolation is enforced ONCE, in the Core.
        """
        # surface is derived from the principal (mcp calls carry source="mcp"); an
        # unauthenticated internal call has principal=None and is still "internal".
        surface = principal.source if principal else "internal"
        start = time.perf_counter()
        try:
            spec = self.__registry.get(name)
            if spec is None:
                raise ToolNotFoundError(f"unknown tool '{name}'")

            # Principal gate: isolation moved UP into the Core. Refuse before validating or
            # executing so a principal-scoped tool can never run without a caller identity.
            if spec.requires_principal and principal is None:
                raise PrincipalRequiredError(f"tool '{name}' requires a principal")

            # spec.input_schema is the JSON Schema advertised to external clients (Phase 2
            # tools/list); the MCP server validates raw JSON at its own boundary, so the core
            # validates once here.
            try:
                typed_args = spec.args_model.model_validate(args)
            except ValidationError as exc:
                raise ToolInputError(f"invalid args for tool '{name}': {exc}") from exc

            # user_id is threaded per-call (NOT bound to this cached-singleton server)
            # so concurrent users never see each other's documents.
            user_id = principal.user_id if principal else ""

            out = spec.handler(typed_args, self.__config, user_id)
        except BaseException as exc:
            # Record the failed call, then re-raise unchanged — the MCP surface and the
            # internal shim still see the exact same exception.
            duration_s = time.perf_counter() - start
            record_tool_call(name, surface, classify_outcome(exc), duration_s)
            raise

        duration_s = time.perf_counter() - start
        record_tool_call(name, surface, classify_outcome(None), duration_s)

        return ToolResult(
            tool_name = name,
            content = [{"type": "text", "text": out}],
            duration_ms = duration_s * 1000.0,
        )

    def execute_tool_call(self, name: str, message: str, user_id: str = "") -> str:
        # Legacy string-in/string-out shim over call_tool, kept so the internal planner
        # (nodes.py) is untouched. Removed in Phase 2 once the agent speaks call_tool.
        spec = self.__registry.get(name)
        if spec is None:
            return ""

        # The planner emits {tool, message}; map `message` -> the tool's `query` arg.
        # `identity` takes no fields, so it validates against {} and ignores `message`.
        raw_args: Dict[str, str] = {} if spec.args_model is IdentityArgs else {"query": message}
        principal = Principal(user_id, "internal") if user_id else None

        try:
            return self.call_tool(name, raw_args, principal).text()
        except PrincipalRequiredError:
            # A principal-scoped tool (search_user_documents) was routed on the internal
            # path with no user_id. In the real HTTP flow the JWT guarantees a user_id, so
            # this means the graph ran outside the authenticated path — an auth/wiring bug,
            # not a normal user state. Degrade to "" so no other tenant's data leaks, but
            # log it: if this ever fires in prod it should be visible. call_tool still
            # raises for the external MCP surface (Phase 2), which maps it to a protocol error.
            _LOGGER.warning(
                "principal-scoped tool '%s' called without user_id — skipping", name)
            return ""


def get_mcp_client(
        chat_model: str = "gemini-2.5-flash",                                   # Switched from gpt-4o-mini to Gemini
        temperature: float = 0,
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        embedding_dimension: int = 384,
        reranking_model: str = "BAAI/bge-reranker-v2-m3",
        reranking_threshold: float = -5,
    ):
    key = (
        chat_model,
        temperature,
        embedding_model,
        embedding_dimension,
        reranking_model,
        reranking_threshold,
    )
    if key not in _MCP_DICT:
        _MCP_DICT[key] = MCPServer(
            chat_model = chat_model,
            temperature = temperature,
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension,
            reranking_model = reranking_model,
            reranking_threshold = reranking_threshold,
        )
    return _MCP_DICT[key]
