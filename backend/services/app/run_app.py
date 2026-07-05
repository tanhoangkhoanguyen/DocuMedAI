from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from langchain_core.messages import AIMessage

import uvicorn, warnings
warnings.filterwarnings("ignore")

from logger import get_logger
from services.app.auth_api import auth_router
from services.app.chat_api import chat_router
from services.app.documents_api import upload_documents_router
from services.app.chatbot_workspace import ChatbotWorkspace
from services.chatbot.constants.schemas import GraphState
from services.chatbot.workflow import build_graph
from services.documents_upload.worker import REDIS_SETTINGS


_LOGGER = get_logger(
    name = "app", 
    level = "INFO"
)


def _build_graph():
    return build_graph(
        # chat_model = "gpt-4o-mini",                                              # Switched to Gemini (Google GenAI)
        chat_model = "gemini-2.5-flash",
        temperature = 0,
        embedding_model = "sentence-transformers/all-MiniLM-L6-v2",
        embedding_dimension = 384,
        reranking_model = "BAAI/bge-reranker-v2-m3",
        max_workers = 4,
        topic_threshold = -5,
        qdrant_threshold = 0.25,
        reranking_threshold = -5,
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

    # arq pool for enqueuing async document-ingestion jobs (la-doc-worker).
    from arq import create_pool
    app.state.arq_pool = await create_pool(REDIS_SETTINGS)
    _LOGGER.info("Chatbot FastAPI on.")

    # Start serving requests
    yield

    # Shutdown
    workspace.close()
    try:
        await app.state.arq_pool.close()
    except Exception:
        pass


app = FastAPI(title = "DocuMedAI Chatbot", lifespan = lifespan)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(upload_documents_router)


# {
#   "user_info": {
#     "user_id": "2b656bec-983f-571b-88b3-9cea12d3e654",
#     "username": "Admin",
#     "email": "admin@gmail.com",
#     "password": "Admin123",
#     "plan": "Free"
#   },
#   "chat_history": [
#     {
#       "type": "human",
#       "content": "Hi chatbot"
#     }
#   ],
#   "shortterm_memory": []
# }
@app.post("/chatbot")
def chatbot(payload: GraphState, thread_id: str = "12345") -> JSONResponse:
    try:
        result = app.state.graph.invoke(
            input = payload,
            config = {"configurable": {"thread_id": thread_id}},
        )
    except Exception as e:
        _LOGGER.exception("Graph invoke failed: %s", e)
        return JSONResponse({"error": str(e)}, status_code = 500)

    if not result.get("chat_history"):
        return JSONResponse({"error": "Empty chat history"}, status_code = 500)

    ai_response = result["chat_history"][-1]
    if isinstance(ai_response, AIMessage):
        chatbot_response = ai_response.content
    else:
        chatbot_response = "Sorry, I didn't understand that."
    return JSONResponse({"reply": chatbot_response}, status_code = 200)

@app.get("/health", response_class = PlainTextResponse)
def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


if __name__ == "__main__":
    uvicorn.run(
        "services.app.run_app:app",
        host = "0.0.0.0",
        port = 2010,
        reload = True,
    )