from fastapi import FastAPI
from contextlib import asynccontextmanager

from services.chatbot.app.route import router
from services.chatbot.core.workflow import build_graph

import os, uvicorn
from dotenv import load_dotenv
load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    chat_model = "gpt-4o-mini"
    embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
    reranking_model = "BAAI/bge-reranker-v2-m3"
    max_workers = max(1, os.cpu_count() - 3)
    
    graph = build_graph(
        chat_model = chat_model,
        embedding_model = embedding_model,
        reranking_model = reranking_model,
        max_workers = max_workers
    )
    router.graph = graph
    yield


app = FastAPI(
    title="Law Advisory Chatbot API",
    lifespan=lifespan,
    docs_url="/",
    redoc_url=None,
    openapi_url="/openapi.json"
)


app.include_router(router)

@app.get("/")
async def read_root():
    return {"message": "Law Advisory Chatbot API is running"}

if __name__ == "__main__":
    chatbot_service_port = int(os.getenv("CHATBOT_SERVICE_PORT", 9004))
    uvicorn.run(
        "services.chatbot.app.main:app",
        host="0.0.0.0",
        port=chatbot_service_port,
        reload=True
    )