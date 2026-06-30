import pytest

from services.chatbot.constants.schemas import GraphState, UserInfo


pytestmark = pytest.mark.integration


def test_chatbot_route(api_client):
    payload = GraphState(
        user_info=UserInfo(
            user_id="123456",
            username="test",
            email="test@example.com",
            password="123456",
            plan="Free",
        ),
        chat_history=[{
            "type": "human",
            "content": "Hi chatbot",
        }],
        shortterm_memory=[],
    )
    res = api_client.post("/chatbot", json=payload.model_dump(mode="json"))
    assert res.status_code == 200
    assert res.json() == {"reply": "AI reply"}
