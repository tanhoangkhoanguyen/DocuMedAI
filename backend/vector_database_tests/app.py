# uvicorn main:app --host 0.0.0.0 --port 2010 --reload
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

FAKE_DOCS = {
    "United States history": "The United States was founded in 1776...",
    "chatbot": "A chatbot is an AI program that simulates conversation...",
    "default": "Here is some general information...",
}

class QueryRequest(BaseModel):
    query: str

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/retrieve")
def retrieve(request: QueryRequest):
    for keyword, doc in FAKE_DOCS.items():
        if keyword.lower() in request.query.lower():
            return {"query": request.query, "result": doc}
    return {"query": request.query, "result": FAKE_DOCS["default"]}