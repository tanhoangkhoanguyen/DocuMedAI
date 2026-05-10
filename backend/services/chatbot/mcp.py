from typing import Dict, Optional, Sequence

from services.chatbot.constants.schemas import McpToolDefinition, ToolParameter
from services.chatbot.tools.medical_supporter import MedicalSupporter


def example_tool(_payload: ToolParameter) -> str:
    return "Hello mcp"

def medical_support_tool(_payload: ToolParameter) -> str:
    client = MedicalSupporter()
    return f"[medical_support_tool]: {client.run()}"


_BUILTIN_MCP_TOOLS: tuple[McpToolDefinition, ...] = (
    McpToolDefinition(
        name = "example_tool",
        description = "Example MCP tool; returns a fixed hello string.",
        handler = example_tool,
    ),
)


class MCPServer:
    """
    In-memory tool registry for the planner catalog and execution by tool name.
    """

    def __init__(self, extra_tools: Optional[Sequence[McpToolDefinition]] = None):
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

    def execute_tool_call(self, name: str, message: str) -> str:
        spec = self.__registry.get(name)
        if spec is None:
            return ""
        return spec.handler(
            ToolParameter(
                message = message
            ))
