
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import datetime, timezone

class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str

class Usage(BaseModel):
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    model_used: Optional[str] = None
    cost_usd: Optional[float] = None

class ChatRequest(BaseModel):
    chat_id: str
    request_id: Optional[str] = None
    message: Message
    metadata: Optional[Dict[str, Any]] = None
    stream: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ChatSession(BaseModel):
    chat_id: str
    chat_names: Optional[List[str]] = None
    messages: List[Message] = Field(default_factory=list)
    summary: Optional[str] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Accept legacy payloads that used "chat_name": "string"
    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_names(cls, data: Any):
        if isinstance(data, dict):
            if "chat_names" not in data and "chat_name" in data:
                v = data.get("chat_name")
                if isinstance(v, str):
                    data["chat_names"] = [v]
                elif isinstance(v, list):
                    data["chat_names"] = v
                else:
                    data["chat_names"] = None
                # optional: drop legacy key to avoid re-dumping it
                data.pop("chat_name", None)
        return data
