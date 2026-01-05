from typing import Annotated, List, Dict, Set, Optional
from pydantic import BaseModel, Field

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

class UnitState(BaseModel):
    context: str
    messages: List[str]
    instruction: str

class GraphState(BaseModel):
    chat_history: Annotated[List[AnyMessage], add_messages]
    global_context: Optional[List[str]] = Field(default_factory = list)
    global_instruction: Optional[str] = ""
    user_inputs: Optional[List[UnitState]] = Field(default_factory = list)
    current_node: Optional[str] = "0"
    ancestors: Optional[Set[str]] = Field(default_factory = set)

class MessageAnalysisState(BaseModel):
    user_inputs: List[UnitState]

class NodeControllerState(BaseModel):
    unit: List[int]

class LLMInvokeState(BaseModel):
    unit: List[str]

class UserInfo(BaseModel):
    user_id: str
    user_name: str
    password: str
    plan: str

class UserData(BaseModel):
    user_id: str
    chat_id: List[str]

class UserChat(BaseModel):
    chat_id: str
    chat_name: str
    chat_history: List[Dict]