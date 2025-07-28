from typing import TypedDict, Annotated, List, Optional
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import Field


class State(TypedDict):
    messages: Annotated[List[AnyMessage], add_messages]
    topic_id: Optional[int]
    user_intruction: Optional[str]


class IntOuput(TypedDict):
    id: int