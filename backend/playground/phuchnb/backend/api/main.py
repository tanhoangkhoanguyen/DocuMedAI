# api/main.py (ensure this exists and runs)
import contextlib
import asyncio
from typing import Dict, Any
from fastapi import FastAPI, Depends
from fastapi.responses import StreamingResponse
from controller.system import System
from constants.schemas import ChatRequest
from graph import simple_graph

app = FastAPI()
_system = None
_worker_task = None

async def get_system() -> System:
    global _system
    if _system is None:
        _system = System()
    return _system

async def _token_gen(payload: Dict[str, Any]):
    async for tok in simple_graph.astream(payload):
        yield tok

@app.on_event("startup")
async def _startup():
    sys = await get_system()
    global _worker_task
    _worker_task = asyncio.create_task(sys.worker_loop(_token_gen, block_ms=5000))

@app.on_event("shutdown")
async def _shutdown():
    global _worker_task, _sweeper_task, _system
    if _sweeper_task:
        _sweeper_task.cancel()
        with contextlib.suppress(Exception):
            await _sweeper_task
    if _system:
        await _system.stop()   # << cleans stream + cache, then closes redis
    if _worker_task:
        _worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _worker_task


@app.get("/health")
def health(): return {"ok": True}

@app.post("/chat")
async def chat(req: ChatRequest, system: System = Depends(get_system)):
    request_id, gen = await system.enqueue_and_stream(req)
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Request-Id": request_id,
        },
    )
