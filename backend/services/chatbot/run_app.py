from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from langchain_core.messages import AIMessage
from typing import Any, Dict

import uvicorn, warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()

from logger import get_logger
from services.chatbot.app.auth_api import auth_router
from services.chatbot.app.chat_api import chat_router
from services.chatbot.app.chatbot_workspace import ChatbotWorkspace
from services.chatbot.constants.schemas import GraphState
from services.chatbot.workflow import build_graph


_LOGGER = get_logger(
    name = "chatbot_api", 
    level = "INFO"
)


def _build_graph():
    return build_graph(
        chat_model = "gpt-4o-mini",
        temperature = 0,
        embedding_model = "sentence-transformers/all-MiniLM-L6-v2",
        embedding_dimension = 384,
        reranking_model = "BAAI/bge-reranker-v2-m3",
        max_workers = 4,
        topic_threshold = -5,
        qdrant_threshold = 0.25,
        rag_threshold = -5,
        shortterm_memory_size = 5,
        max_revision_cycles = 3,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    # Custom shared data. app.state is an empty object until being assigned
    app.state.graph = _build_graph()
    workspace = ChatbotWorkspace(app.state.graph)
    app.state.workspace = workspace                                                # Reference
    _LOGGER.info("Chatbot FastAPI on.")

    # Start serving requests
    yield

    # Shutdown
    workspace.close()


app = FastAPI(title = "DocuMedAI Chatbot", lifespan = lifespan)
app.include_router(auth_router)
app.include_router(chat_router)


@app.post("/chatbot", response_class = PlainTextResponse)
def chatbot(payload: GraphState, thread_id: str = "12345") -> PlainTextResponse:
    try:
        result = app.state.graph.invoke(
            input = payload,
            config = {"configurable": {"thread_id": thread_id}},
        )
    except Exception as e:
        _LOGGER.exception("Graph invoke failed: %s", e)
        return PlainTextResponse(f"Error: {e}", status_code = 500)

    if not result.get("chat_history"):
        raise ValueError("Empty chat history")

    ai_response = result["chat_history"][-1]
    if isinstance(ai_response, AIMessage):
        chatbot_response = ai_response.content
    else:
        chatbot_response = "Sorry, I didn't understand that."
    return PlainTextResponse(chatbot_response, status_code = 200)

@app.get("/health", response_class = PlainTextResponse)
def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


if __name__ == "__main__":
    uvicorn.run(
        "services.chatbot.run_app:app",
        host = "0.0.0.0",
        port = 2010,
        reload = False,
    )

# Example input
# {
#   "chat_history": [
#     {
#       "type": "human",
#       "content": "Hi chatbot"
#     }
#   ]
# }