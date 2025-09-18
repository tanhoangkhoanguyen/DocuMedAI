import asyncio
from typing import AsyncGenerator, Dict, Any
from controller.system import System
from my_graph import graph  # your existing code

async def token_gen(payload: Dict[str, Any]) -> AsyncGenerator[str, None]:
    async for ev in graph.astream({"chat_id": payload["chat_id"], "message": payload["message"]}):
        if isinstance(ev, str):
            yield ev
        elif isinstance(ev, dict):
            yield ev.get("token") or ev.get("delta") or ev.get("text") or ""

async def main():
    sys = System(consumer_name="worker-1")
    try:
        await sys.worker_loop(token_gen)
    finally:
        await sys.close()

if __name__ == "__main__":
    asyncio.run(main())