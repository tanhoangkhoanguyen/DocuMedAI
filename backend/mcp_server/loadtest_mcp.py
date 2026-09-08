"""
MCP serving-capacity load test:  python -m mcp_server.loadtest_mcp [options]

A DIFFERENT axis from overhead_benchmark.py. That one asks "what does one MCP call cost vs
direct?" on loopback. This asks "how much concurrent load can the MCP server sustain, and
how does tail latency degrade as offered load rises?" — so it is meant to run against a
REMOTE server (a GCP VM), client and server on separate machines, over a real network.

Method (mirrors VectorBench/throughput.py, adapted to the async MCP SDK):
  * OPEN-LOOP: an async scheduler dispatches requests at a TARGET arrival rate (QPS),
    independent of when prior responses return. Each request's latency is measured from its
    SCHEDULED send time, so a saturated server shows up as rising latency (the truth), not
    reduced load (the coordinated-omission lie).
  * A fixed pool of N worker coroutines, each holding its own long-lived ClientSession
    (initialize once, reuse), pulls from a BOUNDED asyncio.Queue. When the server can't keep
    up the queue fills and the scheduler blocks — backpressure, and achieved_rps < target_qps
    is the saturation signal.
  * A QPS LADDER is swept; sustainable throughput is the highest rung where achieved ~ target
    and tail latency stays bounded. A warmup rung is discarded.

Loads `identity` only: pure serving path (JSON-RPC + streamable-HTTP + Tool Core dispatch),
no RAG/LLM, so the number is the PROTOCOL LAYER's capacity, not Qdrant/LLM capacity (a
separate, already-benchmarked concern). No uploaded documents or LLM keys needed.

Run (from a client VM, against the server VM):
  python -m mcp_server.loadtest_mcp --url http://<server-ip>:8090/mcp/ \
      --qps 50 100 200 400 800 --duration 30 --warmup 5 --concurrency 64
"""
import argparse, asyncio, json, os, statistics, time, uuid

import jwt

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


DEFAULT_URL = "http://127.0.0.1:8090/mcp/"
DEFAULT_QPS_LADDER = [50, 100, 200, 400, 800]
_TOOL = "identity"
_ARGS: dict = {}


def _percentile(sorted_vals, q: float):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return round(sorted_vals[idx], 3)


def _bearer(user_id: str) -> dict:
    token = jwt.encode(
        {"type": "local", "id": user_id}, os.environ["AUTH_JWT_SECRET"], algorithm = "HS256")
    return {"Authorization": f"Bearer {token}"}


async def _run_level(url: str, target_qps: float, duration_s: float, concurrency: int) -> dict:
    """Drive `target_qps` req/s for `duration_s` (open-loop) over `concurrency` sessions."""
    interval = 1.0 / target_qps
    work: asyncio.Queue = asyncio.Queue(maxsize = concurrency * 4)   # bounded -> backpressure
    latencies_ms: list = []
    outcomes = {"success": 0, "tool_error": 0, "transport_error": 0}

    async def worker():
        # Each worker owns one persistent session (initialize once, reuse for every call).
        headers = _bearer(f"load-{uuid.uuid4().hex[:8]}")
        async with streamablehttp_client(url, headers = headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                while True:
                    item = await work.get()
                    if item is None:
                        work.task_done()
                        return
                    ideal_send = item
                    try:
                        res = await session.call_tool(_TOOL, _ARGS)
                        outcomes["success" if not res.isError else "tool_error"] += 1
                    except Exception:
                        # A raised exception is a transport/protocol failure (not an isError
                        # tool result) — connection reset, timeout, JSON-RPC error, etc.
                        outcomes["transport_error"] += 1
                    # Latency from the IDEAL send time: a request that waited in the backlog is
                    # charged for the wait, as a real client blocked on a slow server would be.
                    latencies_ms.append((time.perf_counter() - ideal_send) * 1000.0)
                    work.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(concurrency)]

    start = time.perf_counter()
    sent = 0
    while time.perf_counter() - start < duration_s:
        ideal_send = start + sent * interval
        sleep_for = ideal_send - time.perf_counter()
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)
        await work.put(ideal_send)   # blocks if backlog full -> the server is the bottleneck
        sent += 1

    await work.join()                # drain in-flight so their latency counts
    for _ in workers:
        await work.put(None)
    await asyncio.gather(*workers)

    wall = time.perf_counter() - start
    completed = len(latencies_ms)
    latencies_ms.sort()
    return {
        "target_qps": target_qps,
        "achieved_rps": round(completed / wall, 2) if wall > 0 else 0.0,
        "sent": sent,
        "completed": completed,
        "success_rate": round(outcomes["success"] / completed, 4) if completed else None,
        "outcomes": outcomes,
        "median_ms": round(statistics.median(latencies_ms), 3) if latencies_ms else None,
        "p95_ms": _percentile(latencies_ms, 0.95),
        "p99_ms": _percentile(latencies_ms, 0.99),
    }


async def _run(args) -> dict:
    if args.warmup > 0:
        await _run_level(args.url, args.qps[0], args.warmup, args.concurrency)   # discarded

    levels = []
    for qps in args.qps:
        res = await _run_level(args.url, qps, args.duration, args.concurrency)
        print(
            f"target={qps}qps achieved={res['achieved_rps']}rps "
            f"median={res['median_ms']}ms p95={res['p95_ms']}ms p99={res['p99_ms']}ms "
            f"success_rate={res['success_rate']}",
            flush = True,
        )
        levels.append(res)

    return {
        "url": args.url,
        "tool": _TOOL,
        "concurrency": args.concurrency,
        "duration_s": args.duration,
        "levels": levels,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog = "loadtest_mcp", description = "MCP serving-capacity load test (remote)")
    p.add_argument("--url", default = DEFAULT_URL, help = "MCP endpoint (e.g. http://<vm-ip>:8090/mcp/)")
    p.add_argument("--qps", type = float, nargs = "+", default = DEFAULT_QPS_LADDER,
                   help = "QPS ladder to sweep (target arrival rates)")
    p.add_argument("--duration", type = float, default = 30.0, help = "seconds per QPS rung")
    p.add_argument("--warmup", type = float, default = 5.0, help = "discarded warmup seconds")
    p.add_argument("--concurrency", type = int, default = 64,
                   help = "worker coroutines / sessions (the concurrency ceiling)")
    p.add_argument("--out", default = None, help = "write the JSON report here (also printed)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(_run(args))
    text = json.dumps(report, indent = 2)
    if args.out:
        with open(args.out, "w", encoding = "utf-8") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
