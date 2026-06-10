from typing import Any, Dict

import jwt, os
from dotenv import load_dotenv
load_dotenv()


class SupabaseClient():
    def __init__(self) -> None:
        return None
    
    @staticmethod
    def decode_access_token(token: str) -> Dict[str, Any]:
        secret = os.getenv("SUPABASE_JWT_SECRET")
        if not secret:
            raise ValueError("SUPABASE_JWT_SECRET is not configured")
        return jwt.decode(
            token,
            secret,
            algorithms = ["HS256"],
            audience = "authenticated",
        )