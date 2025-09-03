from typing import Annotated, List, Optional
from pydantic import BaseModel, Field

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

class GlobalState(BaseModel):
    chat_history: Annotated[List[AnyMessage], add_messages]
    context: Optional[str] = None
    instruction: Optional[str] = None

class LocalState(BaseModel):
    messages: List[str]
    global_context: Optional[str] = None
    local_context: Optional[str] = None
    instruction: Optional[str] = None
    topic_id: Optional[List[int]] = Field(default_factory=list)
    messages_idx: Optional[int] = None
    answers: Optional[List[str]] = Field(default_factory=list)

class MessageAnalysisState(BaseModel):
    messages: List[str]
    context: str
    instruction: str

class IntentAnalysisState(BaseModel):
    intent: List[int]