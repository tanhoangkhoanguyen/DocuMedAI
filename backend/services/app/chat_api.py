from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Any, Dict, List

from services.app.auth_deps import (
    get_workspace,
    require_bearer_claims
)
from services.app.chatbot_workspace import ChatbotWorkspace
from services.chatbot.constants.schemas import (
    ChatRenameState,
    ChatMessageState
)


chat_router = APIRouter(tags = ["chatbot_workspace"])


@chat_router.get("/chats")
def list_chats(
        claims: Dict[str, Any] = Depends(require_bearer_claims),
        workspace: ChatbotWorkspace = Depends(get_workspace),
    ) -> List[Dict[str, str]]:
    return workspace.list_chats(claims["id"])

@chat_router.post("/chats")
def create_chat(
        claims: Dict[str, Any] = Depends(require_bearer_claims),
        workspace: ChatbotWorkspace = Depends(get_workspace),
    ) -> Dict[str, str]:
    return workspace.create_chat(claims["id"])

@chat_router.patch("/chats/{chat_id}")
def rename_chat(
        chat_id: str,
        body: ChatRenameState,
        claims: Dict[str, Any] = Depends(require_bearer_claims),
        workspace: ChatbotWorkspace = Depends(get_workspace),
    ) -> Dict[str, str]:
    try:
        workspace.rename_chat(claims["id"], chat_id, body.chat_name)
    except PermissionError as exc:
        raise HTTPException(
            status_code = 404,
            detail = str(exc)
        ) from exc
    return {
        "chat_id": chat_id,
        "chat_name": body.chat_name.strip() or "Untitled",
    }

@chat_router.get("/chats/{chat_id}/messages")
def get_messages(
        chat_id: str,
        claims: Dict[str, Any] = Depends(require_bearer_claims),
        workspace: ChatbotWorkspace = Depends(get_workspace),
    ) -> Dict[str, Any]:
    try:
        messages = workspace.get_messages(claims["id"], chat_id)
    except PermissionError as exc:
        raise HTTPException(
            status_code = 404,
            detail = str(exc)
        ) from exc
    return {"chat_id": chat_id, "messages": messages}

@chat_router.post("/chats/{chat_id}/messages")
def post_message( # receive human message
        request: Request,
        chat_id: str,
        body: ChatMessageState,
        claims: Dict[str, Any] = Depends(require_bearer_claims),
        workspace: ChatbotWorkspace = Depends(get_workspace),
    ) -> Dict[str, str]:
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise HTTPException(
            status_code = 503,
            detail = "Graph not initialized"
        )
    try:
        reply = workspace.append_user_and_reply(
            claims["id"],
            claims["username"],
            chat_id,
            body.message.strip(),
            graph,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code = 404,
            detail = str(exc)
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code = 500,
            detail = str(exc)
        ) from exc
    return {"chat_id": chat_id, "reply": reply}
