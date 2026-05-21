from fastapi import APIRouter, Depends, HTTPException
from typing import Any, Dict

import warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()

from logger import get_logger
from services.chatbot.app.auth_deps import (
    claims_sub_email,
    get_workspace,
    mint_access_token,
    require_bearer_claims,
    require_supabase_claims,
)
from services.chatbot.app.chatbot_workspace import ChatbotWorkspace
from services.chatbot.constants.schemas import RegisterState


auth_router = APIRouter(tags = ["authentication"])
_LOGGER = get_logger(name = "auth_api", level = "INFO")


@auth_router.post("/auth/register")
def auth_register(
        body: RegisterState,
        workspace: ChatbotWorkspace = Depends(get_workspace),
    ) -> Dict[str, str]:
    # Create account
    try:
        id, username = workspace.register_user(body.email, body.password, "")
    except ValueError as exc:
        msg = str(exc)
        if "exists" in msg:
            raise HTTPException(
                status_code = 409, 
                detail = "Username already taken"
            ) from exc
        raise HTTPException(
            status_code = 400, 
            detail = "Invalid registration"
        ) from exc
    
    # Create JWT token
    try:
        token = mint_access_token(id, username)
    except ValueError as exc:
        raise HTTPException(
            status_code = 500, 
            detail = str(exc)
        ) from exc
    return { 
        "user_id": id,
        "access_token": token, 
        "token_type": "bearer",
    }

@auth_router.post("/auth/supabase-sync")
def supabase_sync(
        # Before calling supabase_sync, execute
        # - require_supabase_claims
        # - get_workpace
        claims: Dict[str, Any] = Depends(require_supabase_claims),
        workspace: ChatbotWorkspace = Depends(get_workspace),
    ) -> Dict[str, str]:
    sub, email = claims_sub_email(claims)
    try:
        user_id = workspace.ensure_user(sub, email)
    except Exception as exc:
        _LOGGER.exception("supabase_sync failed: %s", exc)
        raise HTTPException(
            status_code = 500,
            detail = "Failed to sync user account",
        ) from exc
    return {"user_id": user_id}

@auth_router.post("/auth/login")
def auth_login(
        body: RegisterState,
        workspace: ChatbotWorkspace = Depends(get_workspace),
    ) -> Dict[str, str]:
    try:
        id, username = workspace.verify_login(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(
            status_code = 401, 
            detail = "Invalid password"
        ) from exc
    try:
        token = mint_access_token(id, username)
    except ValueError as exc:
        raise HTTPException(
            status_code = 500, 
            detail = str(exc)
        ) from exc
    return {
        "user_id": id,
        "access_token": token, 
        "token_type": "bearer",
    }

@auth_router.get("/auth/me")
def auth_me(
        claims: Dict[str, Any] = Depends(require_bearer_claims),
    ) -> Dict[str, str]:
    uid = claims.get("id")
    if not uid:
        raise HTTPException(status_code = 401, detail = "Invalid token claims")
    return {
        "user_id": str(uid),
        "username": str(claims.get("username") or ""),
        "auth": str(claims.get("_auth") or ""),
    }