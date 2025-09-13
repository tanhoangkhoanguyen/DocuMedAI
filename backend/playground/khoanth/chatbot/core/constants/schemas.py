from typing import Annotated, List, Optional
from pydantic import BaseModel, Field

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

class GraphState(BaseModel):
    chat_history: Annotated[List[AnyMessage], add_messages]
    messages: Optional[List[str]] = Field(default_factory=list)
    global_context: Optional[str] = ""
    local_context: Optional[str] = ""
    global_instruction: Optional[str] = ""
    local_instruction: Optional[str] = ""
    topic_id: Optional[List[int]] = Field(default_factory=list)

class MessageAnalysisState(BaseModel):
    messages: List[str]
    context: str
    instruction: str

class IntentAnalysisState(BaseModel):
    intent: List[int]

class LocalSummarizerNode(BaseModel):
    context: str
    instruction: str