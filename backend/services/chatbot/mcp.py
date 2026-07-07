from typing import Dict, Optional, Sequence

from services.chatbot.constants.schemas import McpToolDefinition, ToolParameter
from services.chatbot.tools.medical_supporter import get_medical_supporter
from services.chatbot.tools.user_document_supporter import get_user_document_supporter


def medical_support_tool(payload: ToolParameter) -> str:
    client = get_medical_supporter(payload)
    return f"[medical_support_tool]: {client.run(payload.message)}"


def user_document_tool(payload: ToolParameter) -> str:
    client = get_user_document_supporter(payload)
    return f"[user_document_tool]: {client.run(payload.message, payload.user_id)}"


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


def project_info_tool(payload: ToolParameter) -> str:
    return f"[project_info_tool]: {_ABOUT_DOCUMEDAI}"


_MCP_DICT = {}
_BUILTIN_MCP_TOOLS: tuple[McpToolDefinition, ...] = (
    McpToolDefinition(
        name = "medical_support_tool",
        description = (
            "A tool that retrieve medical terms and provide insight for that term. "
            "Pass a short natural-language query (e.g. 'The cause for X' or 'Definition of X')."
        ),
        handler = medical_support_tool,
    ),
    McpToolDefinition(
        name = "project_info_tool",
        description = (
            "Provides general information about this chatbot and project. Use it to answer "
            "meta questions such as 'What are you?', 'Why do you exist / what is this project "
            "for?', 'Who created you?', or any question about DocuMedAI itself, its purpose, or "
            "its creator. Takes no meaningful input."
        ),
        handler = project_info_tool,
    ),
    McpToolDefinition(
        name = "user_document_tool",
        description = (
            "Retrieve passages from the CURRENT USER's own uploaded documents (their PDFs, "
            "DOCX, or text files) to answer questions grounded in those files. Use this whenever "
            "the user refers to 'my document', 'the file I uploaded', 'my report/record', or "
            "asks something that should be answered from their uploaded content. "
            "Pass a short natural-language query describing what to find."
        ),
        handler = user_document_tool,
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
            extra_tools: Optional[Sequence[McpToolDefinition]] = None
        ):
        self.chat_model = chat_model
        self.temperature = temperature
        self.embedding_model = embedding_model
        self.embedding_dimension = embedding_dimension
        self.reranking_model = reranking_model
        self.reranking_threshold = reranking_threshold

        self.__registry: Dict[str, McpToolDefinition] = {}
        self._build_mcp_registry(extra_tools or ())

    def _build_mcp_registry(self, extra_tools: Sequence[McpToolDefinition]) -> None:
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
        return spec.handler(
            ToolParameter(
                message = message,
                chat_model = self.chat_model,
                temperature = self.temperature,
                embedding_model = self.embedding_model,
                embedding_dimension = self.embedding_dimension,
                reranking_model = self.reranking_model,
                reranking_threshold = self.reranking_threshold,
                user_id = user_id,
            ))

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