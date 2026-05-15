from typing import Dict, Optional, Sequence

from services.chatbot.constants.schemas import McpToolDefinition, ToolParameter
from services.chatbot.tools.medical_supporter import get_medical_supporter


def medical_support_tool(payload: ToolParameter) -> str:
    client = get_medical_supporter(payload)
    return f"[medical_support_tool]: {client.run(payload.message)}"


_BUILTIN_MCP_TOOLS: tuple[McpToolDefinition, ...] = (
    McpToolDefinition(
        name = "medical_support_tool",
        description = (
            "A tool that retrieve medical terms and provide insight for that term. "
            "Pass a short natural-language query (e.g. 'The cause for X' or 'Definition of X')."
        ),
        handler = medical_support_tool,
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
            rag_threshold: float,
            extra_tools: Optional[Sequence[McpToolDefinition]] = None
        ):
        self.chat_model = chat_model
        self.temperature = temperature
        self.embedding_model = embedding_model
        self.embedding_dimension = embedding_dimension
        self.reranking_model = reranking_model
        self.rag_threshold = rag_threshold

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

    def execute_tool_call(self, name: str, message: str) -> str:
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
                rag_threshold = self.rag_threshold,
            ))
