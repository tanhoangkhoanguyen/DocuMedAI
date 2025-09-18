import os
from dotenv import load_dotenv

load_dotenv()

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    redis_password: str = os.getenv("REDIS_PASSWORD")
    redis_db: int = os.getenv("REDIS_DB")
    redis_port: int = os.getenv("REDIS_PORT")
    session_ttl_seconds: int = os.getenv("SESSION_TTL_SECONDS")
    backend_port: int = os.getenv("BACKEND_PORT")
    openai_api_key: str = os.getenv("OPENAI_API_KEY")

settings = Settings()

if __name__ == "__main__":
    print(f"Test settings: ...")
    print(settings)
    print(f"Test settings: ... done")
 