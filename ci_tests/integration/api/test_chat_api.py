import pytest


pytestmark = pytest.mark.integration


def test_chat_crud_and_message(api_client, auth_headers):
    create = api_client.post("/chats", headers=auth_headers)
    assert create.status_code == 200
    chat_id = create.json()["chat_id"]

    listed = api_client.get("/chats", headers=auth_headers)
    assert listed.status_code == 200
    assert any(row["chat_id"] == chat_id for row in listed.json())

    rename = api_client.patch(
        f"/chats/{chat_id}",
        headers=auth_headers,
        json={"chat_name": "Renamed chat"},
    )
    assert rename.status_code == 200
    assert rename.json()["chat_name"] == "Renamed chat"

    empty = api_client.get(f"/chats/{chat_id}/messages", headers=auth_headers)
    assert empty.status_code == 200
    assert empty.json()["messages"] == []

    reply = api_client.post(
        f"/chats/{chat_id}/messages",
        headers=auth_headers,
        json={"message": "hello"},
    )
    assert reply.status_code == 200
    assert reply.json()["reply"] == "AI reply"

    history = api_client.get(f"/chats/{chat_id}/messages", headers=auth_headers)
    assert history.status_code == 200
    assert len(history.json()["messages"]) >= 1


def test_delete_chat(api_client, auth_headers):
    create = api_client.post("/chats", headers=auth_headers)
    assert create.status_code == 200
    chat_id = create.json()["chat_id"]

    deleted = api_client.delete(f"/chats/{chat_id}", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json()["chat_id"] == chat_id

    listed = api_client.get("/chats", headers=auth_headers)
    assert listed.status_code == 200
    assert not any(row["chat_id"] == chat_id for row in listed.json())

    # Deleting again (now unowned) and reading its messages both 404
    assert api_client.delete(f"/chats/{chat_id}", headers=auth_headers).status_code == 404
    assert api_client.get(f"/chats/{chat_id}/messages", headers=auth_headers).status_code == 404


def test_chat_routes_require_auth(api_client):
    assert api_client.get("/chats").status_code == 401
    assert api_client.post("/chats").status_code == 401
    assert api_client.delete("/chats/anything").status_code == 401
