from typing import Annotated, List, Optional
from pydantic import BaseModel, Field
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

import Dict

class GraphState(BaseModel):
    chat_history: Annotated[List[AnyMessage], add_messages]                     # chat_id conversation history
    local_context: Optional[List[str]] = Field(default_factory = list)          # 
    user_inputs: Optional[MessageAnalysisState] = Field(default_factory = list) # Analyzed user input

# LLM formatted outputs
class UnitState(BaseModel):
    context: str
    messages: List[str]
    instruction: str

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