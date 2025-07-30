from fastapi import FastAPI
from contextlib import asynccontextmanager

from services.chatbot.app.route import router
from services.chatbot.core.workflow import build_graph

import os
import uvicorn
from dotenv import load_dotenv
load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    graph = build_graph(model_name='gpt-4o-mini')
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