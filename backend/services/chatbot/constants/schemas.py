from dataclasses import dataclass
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing import Annotated, Any, Callable, Dict, List, Literal, Optional

# ==================== Graph State ====================
class ToolCallState(BaseModel):
    tool: str
    message: str


McpToolHandler = Callable[[Dict[str, Any]], str]
McpCallTool = Callable[[str, Dict[str, Any]], str]


@dataclass(frozen = True)
class McpToolDefinition:
    name: str
    description: str
    handler: McpToolHandler


class ToolParameter(BaseModel):
    message: str


class TaskState(BaseModel):
    context: Optional[str] = None
    message: Optional[str] = None
    feedback: Optional[str] = None
    result: Optional[str] = None


class DraftAgentState(BaseModel):
    reply_text: str = ""


class CritiqueAgentState(BaseModel):
    pass_fail: Literal["pass", "fail"]
    feedback: str = ""


class SubMessageState(BaseModel):
    context: Optional[str] = None
    messages: Optional[List[str]] = None
    instruction: Optional[str] = None


class GraphState(BaseModel):
    chat_history: Annotated[List[AnyMessage], add_messages]
    user_inputs: Optional[List[SubMessageState]] = None
    task_list: Optional[List[List[TaskState]]] = None
    shortterm_memory: Optional[List[str]] = None


# ==================== User Data ====================
class UserData(BaseModel):
    user_id: str
    chat_id: List[str]


class UserChat(BaseModel):
    chat_id: str
    chat_name: str
    chat_history: List[dict]


class UserInfo(BaseModel):
    user_id: str
    user_name: str
    password: str
    plan: str