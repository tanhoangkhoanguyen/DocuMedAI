from typing import Annotated, List, Set, Optional
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