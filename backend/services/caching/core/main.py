import asyncio
from typing import AsyncGenerator, Dict, Any
from controller.system import System
from services.caching.core.mock_graph import simple_graph

async def token_generation(payload: Dict[str, Any]) -> AsyncGenerator[str, None]:
    async for ch in simple_graph.astream({"chat_id": payload["chat_id"], "message": payload["message"]}):
        if isinstance(ch, str):
            yield ch
        elif isinstance(ch, dict):
            yield ch.get("token") or ch.get("delta") or ch.get("text") or ""

async def main():
    system = System()
    try:
        await system.worker_loop(token_generation)
    finally:
        await system.close()

if __name__ == "__main__":
    asyncio.run(main())