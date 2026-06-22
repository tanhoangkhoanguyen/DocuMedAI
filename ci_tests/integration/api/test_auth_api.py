import pytest


pytestmark = pytest.mark.integration


def test_register_and_me(api_client, unique_email):
    payload = {"email": unique_email, "password": "123456"}
    res = api_client.post(
        "/auth/register",
        json=payload,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"

    me = api_client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["user_id"] == body["user_id"]


def test_duplicate_register(api_client, unique_email):
    payload = {"email": unique_email, "password": "123456"}
    assert api_client.post(
        "/auth/register",
        json=payload,
    ).status_code == 200

    res = api_client.post(
        "/auth/register",
        json=payload,
    )
    assert res.status_code == 409


def test_login_valid_credentials(api_client, unique_email):
    payload = {"email": unique_email, "password": "123456"}
    assert api_client.post(
        "/auth/register",
        json=payload,
    ).status_code == 200

    res = api_client.post(
        "/auth/login",
        json=payload,
    )
    assert res.status_code == 200
    assert res.json()["access_token"]


def test_login_invalid_password(api_client, unique_email):
    payload = {"email": unique_email, "password": "123456"}
    assert api_client.post(
        "/auth/register",
        json=payload,
    ).status_code == 200

    res = api_client.post(
        "/auth/login",
        json={"email": unique_email, "password": "wrong-password"},
    )
    assert res.status_code == 401


def test_me_requires_auth(api_client):
    res = api_client.get(
        "/auth/me",
        headers={"Authorization": "Bearer access_token"},
    )
    assert res.status_code == 401


def test_supabase_sync(api_client, supabase_jwt):
    res = api_client.post(
        "/auth/supabase-sync",
        headers={"Authorization": f"Bearer {supabase_jwt}"},
    )
    assert res.status_code == 200
    assert "user_id" in res.json()
