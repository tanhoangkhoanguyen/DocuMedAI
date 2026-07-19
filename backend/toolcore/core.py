from typing import Dict, Optional, Sequence

from pydantic import ValidationError

from toolcore.contracts import (
    IdentityArgs,
    RuntimeConfig,
    SearchMedicalKnowledgeArgs,
    SearchUserDocumentsArgs,
    ToolInputError,
    ToolSpec,
)
from toolcore.tools.medical_supporter import get_medical_supporter
from toolcore.tools.user_document_supporter import get_user_document_supporter


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

    def execute_tool_call(self, name: str, message: str, user_id: str = "") -> str:
        # user_id is threaded per-call (NOT bound to this cached-singleton server)
        # so concurrent users never see each other's documents.
        spec = self.__registry.get(name)
        if spec is None:
            return ""

        # The planner emits {tool, message}; map `message` -> the tool's `query` arg.
        # `identity` takes no fields, so it validates against {} and ignores `message`.
        raw_args: Dict[str, str] = {} if spec.args_model is IdentityArgs else {"query": message}

        # Validate + build the typed args in one pass. spec.input_schema is the JSON
        # Schema advertised to external clients (Phase 2 tools/list); the MCP server
        # will validate raw JSON at its own boundary, so the core validates once here.
        try:
            args = spec.args_model.model_validate(raw_args)
        except ValidationError as exc:
            raise ToolInputError(f"invalid args for tool '{name}': {exc}") from exc

        return spec.handler(args, self.__config, user_id)


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
