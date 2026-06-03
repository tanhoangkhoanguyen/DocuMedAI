from dataclasses import dataclass
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, field_validator
from typing import Annotated, Any, Callable, Dict, List, Literal, Optional


# ==================== User State ====================
class UserInfo(BaseModel):
    user_id: str = "2b656bec-983f-571b-88b3-9cea12d3e654"
    username: str = "Admin"
    email: str = "admin@gmail.com"
    password: str = "Admin123"
    plan: Literal["Free", "Pro"] = "Free"


class UserData(BaseModel):
    user_id: str
    chat_ids: List[str]


class UserChat(BaseModel):
    chat_id: str
    chat_name: str
    chat_history: List[dict]
    shortterm_memory: List[dict]


class RegisterState(BaseModel):
    email: str = Field(min_length = 2, max_length = 80)
    password: str  = Field(min_length = 6, max_length = 128)


class ChatRenameState(BaseModel):
    chat_name: str = Field(min_length = 1, max_length = 200)


class ChatMessageState(BaseModel):
    message: str = Field(min_length = 1, max_length = 32000)


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
    chat_model: str
    temperature: float
    embedding_model: str
    embedding_dimension: int
    reranking_model: str
    reranking_threshold: float


class TaskState(BaseModel):
    context: str = Field(default_factory = str)
    message: str = Field(default_factory = str)
    result: str = Field(default_factory = str)


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
    user_info: UserInfo = Field(default_factory = lambda: UserInfo())
    chat_history: Annotated[List[AnyMessage], add_messages] = Field(default_factory = list)
    user_inputs: Optional[List[SubMessageState]] = None
    task_list: Optional[List[List[TaskState]]] = Field(default_factory = list)
    shortterm_memory: List[str] = Field(default_factory = list)

    @field_validator("shortterm_memory", mode = "before")
    @classmethod
    def _default_shortterm(cls, v):
        if v is None:
            return []
        return v