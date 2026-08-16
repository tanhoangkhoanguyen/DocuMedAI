import uuid, pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from unittest.mock import MagicMock
from services.app.run_app import app


@pytest.fixture(scope="session")
def mock_graph():
    graph = MagicMock()
    graph.invoke.return_value = {
        "chat_history": [
            HumanMessage(content="hello"),
            AIMessage(content="AI reply"),
        ],
        "shortterm_memory": [
            "User Message: hello\nAI Message: AI reply",
        ],
    }
    return graph


@pytest.fixture(scope="session")
def api_client(mock_graph):
    # Mongo/Redis connections are shared global singletons. If each test creates 
    # and closes its own app client, the first test will close those shared connections. 
    # Later tests then fail because Mongo/Redis are already closed. Therefore, 
    # the app/client should be started once for the entire test session and closed only after all tests finish.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("services.app.run_app._build_graph", lambda: mock_graph)
        with TestClient(app) as client:
            yield client


@pytest.fixture
def unique_email():
    return f"test-{uuid.uuid4().hex[:8]}@example.com"


@pytest.fixture
def auth_headers(api_client, unique_email):
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
    return {"Authorization": f"Bearer {res.json()['access_token']}"}
