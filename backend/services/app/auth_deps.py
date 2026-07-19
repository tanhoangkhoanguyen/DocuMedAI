from typing import Any, Dict, Optional

import os, jwt, time
from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from services.app.chatbot_workspace import ChatbotWorkspace
from utils.pattern_cipher import PatternCipher
from utils.supabase_client import SupabaseClient


security = HTTPBearer(auto_error = False)
_SECRET = os.getenv("AUTH_JWT_SECRET")


async def require_supabase_claims(
        # HTTPAuthorizationCredentials(
        #     scheme="Bearer",
        #     credentials="<jwt token>"
        # )
        creds: Optional[HTTPAuthorizationCredentials] = Depends(security)
    ):
    if not creds or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code = 401,
            detail = "Missing bearer token"
        )
    try:
        return SupabaseClient.decode_access_token(creds.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code = 401,
            detail = f"Invalid token: {exc}"
        ) from exc

def get_workspace(request: Request) -> ChatbotWorkspace:
    ws = getattr(request.app.state, "workspace", None)
    if ws is None:
        raise HTTPException(
            status_code = 503,
            detail = "Workspace store not initialized"
        )
    return ws

def claims_sub_email(claims: Dict[str, Any]) -> tuple[str, str]:
    sub = claims.get("sub")
    if not sub or not isinstance(sub, str):
        raise HTTPException(
            status_code = 401,
            detail = "Token missing sub"
        )
    email = claims.get("email") or ""
    if not isinstance(email, str):
        email = str(email)
    return sub, email

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
    # Peek the claims without verifying the signature to decide which verifier
    # owns this token, then run ONLY that verifier so its real error surfaces.
    try:
        unverified = jwt.decode(token, options = {"verify_signature": False})
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code = 401,
            detail = f"Malformed token: {exc}"
        ) from exc

    # Local tokens are minted with "type": "local" (see mint_access_token).
    if unverified.get("type") == "local":
        if not _SECRET:
            raise HTTPException(
                status_code = 401,
                detail = "AUTH_JWT_SECRET is not configured"
            )
        try:
            p = jwt.decode(token, _SECRET, algorithms = ["HS256"])
            if p.get("type") != "local" or not p.get("id"):
                raise jwt.InvalidTokenError("token missing type=local or id")
            return {**p, "_auth": "local"}
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code = 401,
                detail = f"Invalid local token: {exc}"
            ) from exc

    # Otherwise treat it as a Supabase token.
    try:
        c = SupabaseClient.decode_access_token(token)
        sub = c.get("sub")
        if not sub or not isinstance(sub, str):
            raise jwt.InvalidTokenError("missing sub")
        cipher = PatternCipher()
        uid = cipher.stable_supabase_user_id(sub)
        email = c.get("email") or ""
        username = ChatbotWorkspace._extract_email_name(email)
        return {**c, "_auth": "supabase", "id": uid, "username": username}
    except Exception as exc:
        raise HTTPException(
            status_code = 401,
            detail = f"Invalid Supabase token: {exc}"
        ) from exc

async def require_bearer_claims(
        creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ) -> Dict[str, Any]:
    if not creds or creds.scheme.lower() != "bearer":                  # Supabase sync sends the credentails through frontend
        raise HTTPException(
            status_code = 401,
            detail = "Missing bearer token"
        )
    return decode_bearer_any(creds.credentials)
