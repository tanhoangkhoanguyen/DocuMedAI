from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routes import chatbot
from .logger import *

startup_logger()
logger = get_logger()

app = FastAPI(
    title = "My Custom API",
    description = "This is REST API",
    version = "1.0.0"
)

origins = ["*"]  # Use specific origins in production
app.add_middleware(
    CORSMiddleware,
    allow_origins = origins,
    allow_credentials = True,
    allow_methods = ["*"],
    allow_headers = ["*"]
)

app.include_router(chatbot.router, prefix = "/api/chatbot_test", tags = ["chatbot_test"])

@app.get("/")
def root():
    return {"message": "Welcome to REST API!"}