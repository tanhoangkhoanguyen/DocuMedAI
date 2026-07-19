import os

import jwt
import pytest

from utils.supabase_client import SupabaseClient


def test_decode_access_token_valid():
    secret = "test-supabase-secret"
    os.environ["SUPABASE_JWT_SECRET"] = secret
    token = jwt.encode(
        {
            "sub": "supabase-123456",
            "email": "test@example.com",
            "aud": "authenticated",
        },
        secret,
        algorithm="HS256",
    )
    claims = SupabaseClient.decode_access_token(token)
    assert claims["sub"] == "supabase-123456"
    assert claims["aud"] == "authenticated"


def test_decode_access_token_missing_secret(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    with pytest.raises(ValueError, match="SUPABASE_JWT_SECRET"):
        SupabaseClient.decode_access_token("not-a-real-token")
