from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from langchain_core.messages import AIMessage

import uvicorn, warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()

from logger import get_logger
from services.chatbot.constants.schemas import GraphState
from services.chatbot.workflow import build_graph


_LOGGER = get_logger(name = "chatbot_api", level = "INFO")


def _build_graph():
    return build_graph(
        chat_model = "gemini-2.5-flash",
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
    app.state.graph = _build_graph()
    _LOGGER.info("Chatbot graph compiled for FastAPI.")
    yield


app = FastAPI(title = "DocuMedAI Chatbot", lifespan = lifespan)


@app.post("/chat", response_class = PlainTextResponse)
def chat(payload: GraphState, thread_id: str = "12345") -> PlainTextResponse:
    try:
        result = app.state.graph.invoke(
            input = payload,
            config = {"configurable": {"thread_id": thread_id}},
        )
    except Exception as e:
        _LOGGER.exception("Graph invoke failed: %s", e)
        return PlainTextResponse(f"Error: {e}", status_code = 500)

    history = result.get("chat_history") or []
    if not history:
        return PlainTextResponse("Sorry, I didn't understand that.", status_code = 200)

    ai_response = history[-1]
    if isinstance(ai_response, AIMessage):
        return PlainTextResponse(str(ai_response.content))
    return PlainTextResponse("Sorry, I didn't understand that.")


@app.get("/health", response_class = PlainTextResponse)
def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


if __name__ == "__main__":
    uvicorn.run(
        "services.chatbot.app:app",
        host = "0.0.0.0",
        port = 6107,
        reload = False,
    )