from fastapi import FastAPI
from contextlib import asynccontextmanager

from services.chatbot.app.route import router
from services.chatbot.core.workflow import build_graph


@asynccontextmanager
async def lifespan(app: FastAPI):

    graph = build_graph(model_name='gpt-4o-mini')
    router.graph = graph
    yield


app = FastAPI(
    title="Law Advisory Chatbot API",
    lifespan=lifespan
)


app.include_router(router)

@app.get("/")
async def read_root():
    return {"message": "Law Advisory Chatbot API is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "services.chatbot.app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )