from typing import Annotated, List, Optional
from pydantic import BaseModel

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class LawAgentState(BaseModel):
    messages: Annotated[List[AnyMessage], add_messages]
    topic_id: Optional[int] = None
    user_instruction: Optional[str] = None

class TopicIDResponse(BaseModel):
    id: int