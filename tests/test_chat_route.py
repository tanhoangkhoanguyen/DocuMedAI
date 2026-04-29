# from fastapi import FastAPI
# from fastapi.testclient import TestClient

# from backend.services.chatbot.app.route import router
# from backend.services.chatbot.app.request_schemas import ChatResponse


# app = FastAPI()
# app.include_router(router)


# class FakeGraph:
#     def invoke(self, input, config=None):
#         return {
#             "chat_history": [{"content": "Hi from fake graph"}],
#             "topic_id": 123,
#         }


# router.graph = FakeGraph()
# client = TestClient(app)


# def test_chat_endpoint_returns_valid_response():
#     payload = {"session_id": "test-session", "message": "Hello"}
#     response = client.post("/chat", json=payload)

#     assert response.status_code == 200

#     data = response.json()
#     ChatResponse.model_validate(data)
#     assert data["session_id"] == "test-session"
#     assert data["topic_id"] == 123
#     assert "Hi from fake graph" in data["response"]