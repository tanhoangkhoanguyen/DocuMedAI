from typing import Any, Dict, Optional

import os, jwt, time
from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.app.chatbot_workspace import ChatbotWorkspace


security = HTTPBearer(auto_error = False)
_SECRET = os.getenv("AUTH_JWT_SECRET")


def get_workspace(request: Request) -> ChatbotWorkspace:
    ws = getattr(request.app.state, "workspace", None)
    if ws is None:
        raise HTTPException(
            status_code = 503,
            detail = "Workspace store not initialized"
        )
    return ws

def mint_access_token(id: str, username: str) -> str:
    if not _SECRET:
        raise ValueError("AUTH_JWT_SECRET is not configured")
    payload = {
        "type": "local",
        "id": id,
        "username": username,
        "exp": int(time.time()) + 1 * 86400,                           # 1 day
    }
    token = jwt.encode(payload, _SECRET, algorithm = "HS256")
    return token if isinstance(token, str) else token.decode("utf-8")

def decode_bearer_any(token: str) -> Dict[str, Any]:
    """
    Verify a local HS256 bearer token and return its claims, normalized with
    `_auth` so downstream callers can tell where the identity came from.

    Kept as `decode_bearer_any` (rather than `decode_local`) because it is the
    single shared verifier for every bearer surface — FastAPI routes and the MCP
    server both call it. Local tokens are now the only kind issued.
    """
    if not _SECRET:
        raise HTTPException(
            status_code = 401,
            detail = "AUTH_JWT_SECRET is not configured"
        )
    try:
        p = jwt.decode(token, _SECRET, algorithms = ["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code = 401,
            detail = f"Invalid local token: {exc}"
        ) from exc

    if p.get("type") != "local" or not p.get("id"):
        raise HTTPException(
            status_code = 401,
            detail = "Invalid local token: missing type=local or id"
        )
    return {**p, "_auth": "local"}

async def require_bearer_claims(
        creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ) -> Dict[str, Any]:
    if not creds or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code = 401,
            detail = "Missing bearer token"
        )
    return decode_bearer_any(creds.credentials)
