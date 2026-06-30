import json, pytest


pytestmark = pytest.mark.integration


def _parse_sse(text: str):
    events = []
    for line in text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[len("data:"):].strip()))
    return events


def test_message_stream(api_client, auth_headers):
    create = api_client.post("/chats", headers=auth_headers)
    assert create.status_code == 200
    chat_id = create.json()["chat_id"]

    res = api_client.post(
        f"/chats/{chat_id}/messages/stream",
        headers=auth_headers,
        json={"message": "hello"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(res.text)
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert "".join(tokens) == "AI reply"

    done = [e for e in events if e["type"] == "done"]
    assert len(done) == 1
    assert done[0]["chat_id"] == chat_id


def test_message_stream_requires_auth(api_client):
    res = api_client.post(
        "/chats/some-chat-id/messages/stream",
        json={"message": "hello"},
    )
    assert res.status_code == 401
