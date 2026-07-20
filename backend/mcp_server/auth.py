"""
MCP auth: turn a bearer token into a `Principal(source="mcp")`.

This is the ONLY auth surface for the external MCP server. Internal `source="internal"`
callers never reach here — they build their own principal in `toolcore.core`.

The JWT logic is NOT forked: we reuse `services.app.auth_deps.decode_bearer_any`, which
already verifies both local HS256 tokens (`type=local`, `id`) and Supabase tokens
(normalized to `id` via `stable_supabase_user_id`). That verifier raises FastAPI's
`HTTPException` on failure; since the MCP adapter is not FastAPI, we translate it into a
transport-neutral `MCPAuthError` the callers map to their own error surface:

    HTTP  — 401 at the ASGI middleware, before the session manager.
    stdio — abort at process start (a broken MCP_AUTH_TOKEN is a misconfiguration).

Token policy:
    absent / empty  -> None            (anonymous: identity + medical search still work;
                                         the Core refuses search_user_documents)
    valid           -> Principal(...)  (source="mcp")
    present-invalid -> raise MCPAuthError
"""
from typing import Optional

from fastapi import HTTPException

from services.app.auth_deps import decode_bearer_any
from toolcore.contracts import Principal


class MCPAuthError(Exception):
    """A present-but-invalid bearer token. Transport-neutral (no FastAPI dependency)."""


def principal_from_token(token: Optional[str]) -> Optional[Principal]:
    """
    Verify a bearer token and build an MCP principal, or None when no token is present.

    A token that is present but fails verification raises `MCPAuthError` — it is a
    misconfigured client, not an anonymous one.
    """
    if not token:
        return None                                # anonymous — never touches the verifier

    try:
        claims = decode_bearer_any(token)
    except HTTPException as exc:                   # 401 from the shared verifier
        raise MCPAuthError(str(exc.detail)) from exc

    user_id = claims.get("id")
    if not user_id:                                # verifier normalizes both token types to `id`
        raise MCPAuthError("Token missing normalized user id")

    return Principal(user_id = user_id, source = "mcp")
