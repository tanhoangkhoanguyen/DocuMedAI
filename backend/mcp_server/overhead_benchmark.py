"""
MCP protocol-overhead benchmark:  python -m mcp_server.overhead_benchmark [options]

Answers the headline question: *what does the MCP protocol cost vs calling the Tool Core
directly?* It times the SAME tool + args two ways —

    direct : get_mcp_client().call_tool(...)            in-process, no transport
    mcp    : ClientSession.call_tool(...) over HTTP      full streamable-HTTP round trip

— and reports each arm's P50/P95/P99 plus the overhead delta at EACH percentile (ms + %).
Reporting overhead at the tail (p95/p99), not just the median, matters because protocol
cost is usually worst under jitter.

This is the LOCAL, single-in-flight probe: it isolates pure serialization + JSON-RPC +
streamable-HTTP framing cost, so it is meant to run on loopback where network RTT ~ 0.
Serving CAPACITY under concurrent load (throughput vs offered QPS, tail latency under load,
error breakdown) is a separate axis — see loadtest_mcp.py, which runs against a remote VM.

Uses `identity` by default (no RAG/LLM, no uploaded docs) so the number is transport cost,
not retrieval cost. Coordinated-omission discipline is echoed from VectorBench/
(latency measured from each request's ideal send time), not imported.
"""
import argparse, asyncio, json, os, statistics, time, uuid

import jwt

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from toolcore.contracts import Principal
from toolcore.core import get_mcp_client


DEFAULT_URL = "http://127.0.0.1:8090/mcp/"   # trailing slash: Mount 307-redirects /mcp -> /mcp/
DEFAULT_TOOLS = ("identity",)
_ARGS_FOR = {
    "identity": {},
}
_PERCENTILES = ("p50", "p95", "p99")


def _percentile(sorted_vals, q: float) -> float:
    # Nearest-rank via truncated index — matches VectorBench/throughput.py so the
    # two harnesses report percentiles the same way.
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return round(sorted_vals[idx], 3)


def _summarize(latencies_ms) -> dict:
    lat = sorted(latencies_ms)
    return {
        "count": len(lat),
        "p50": round(statistics.median(lat), 3) if lat else 0.0,
        "p95": _percentile(lat, 0.95),
        "p99": _percentile(lat, 0.99),
    }


def _bearer(user_id: str) -> dict:
    token = jwt.encode(
        {"type": "local", "id": user_id}, os.environ["AUTH_JWT_SECRET"], algorithm = "HS256")
    return {"Authorization": f"Bearer {token}"}


def _bench_direct(tool: str, args: dict, n: int, warmup: int, qps: float) -> list:
    """Open-loop in-process arm. No transport — the baseline the MCP arm is compared to."""
    core = get_mcp_client()
    principal = Principal(f"bench-{uuid.uuid4().hex[:8]}", "internal")
    interval = 1.0 / qps
    latencies = []
    start = time.perf_counter()
    for i in range(warmup + n):
        ideal_send = start + i * interval
        now = time.perf_counter()
        if ideal_send > now:
            time.sleep(ideal_send - now)
        core.call_tool(tool, args, principal = principal)
        if i >= warmup:
            latencies.append((time.perf_counter() - ideal_send) * 1000.0)
    return latencies


async def _bench_mcp(url: str, tool: str, args: dict, n: int, warmup: int, qps: float) -> list:
    """Open-loop MCP arm over one long-lived streamable-HTTP session (initialize once)."""
    headers = _bearer(f"bench-{uuid.uuid4().hex[:8]}")
    interval = 1.0 / qps
    latencies = []
    async with streamablehttp_client(url, headers = headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            start = time.perf_counter()
            for i in range(warmup + n):
                ideal_send = start + i * interval
                now = time.perf_counter()
                if ideal_send > now:
                    await asyncio.sleep(ideal_send - now)
                await session.call_tool(tool, args)
                if i >= warmup:
                    latencies.append((time.perf_counter() - ideal_send) * 1000.0)
    return latencies


def _overhead(direct: dict, mcp: dict) -> dict:
    # Overhead at every percentile, not just the median: framing/serialization cost tends to
    # be worst at the tail, which is the more honest number to report.
    out = {}
    for p in _PERCENTILES:
        d, m = direct[p], mcp[p]
        out[p] = {
            "overhead_ms": round(m - d, 3),
            "overhead_pct": round((m - d) / d * 100.0, 1) if d else None,
        }
    return out


async def _run(args) -> dict:
    tools = args.tool or list(DEFAULT_TOOLS)
    per_tool = {}
    for tool in tools:
        call_args = _ARGS_FOR.get(tool, {})
        direct = _summarize(_bench_direct(tool, call_args, args.n, args.warmup, args.qps))
        mcp = _summarize(
            await _bench_mcp(args.url, tool, call_args, args.n, args.warmup, args.qps))
        per_tool[tool] = {"direct": direct, "mcp": mcp, "overhead": _overhead(direct, mcp)}

    return {
        "url": args.url,
        "n": args.n,
        "warmup": args.warmup,
        "qps": args.qps,
        "tools": per_tool,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog = "overhead_benchmark", description = "MCP protocol-overhead benchmark (local)")
    p.add_argument("--tool", action = "append", help = "tool to bench (repeatable; default: identity)")
    p.add_argument("--n", type = int, default = 10000, help = "timed calls per tool per arm")
    p.add_argument("--warmup", type = int, default = 20, help = "discarded warmup calls")
    p.add_argument("--qps", type = float, default = 50.0, help = "open-loop send rate (single in-flight)")
    p.add_argument("--url", default = DEFAULT_URL, help = "MCP streamable-HTTP endpoint")
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
