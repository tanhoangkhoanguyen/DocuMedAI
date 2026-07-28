"""
Per-call observability for the Tool Core. One place owns the metric objects and the
structured log line so `call_tool` stays a plain control-flow read.

Every tool call — internal (LangGraph agent) or external (MCP server) — crosses the same
`call_tool`, so recording here yields one comparable record per call, tagged by `surface`.
That comparability is the whole point: it lets the internal fast path and the MCP protocol
path be measured on one metric.

Emitted twice per call:
- Prometheus (default registry, scraped by a later phase's /metrics endpoint):
  `toolcore_tool_calls_total` and `toolcore_tool_call_duration_seconds`, both labelled
  {tool, surface, outcome}.
- A JSON log line (orjson) through the shared logger — Loki-friendly, one object per call.
"""
from typing import Optional

import orjson
from prometheus_client import Counter, Histogram

from logger import get_logger
from toolcore.contracts import PrincipalRequiredError, ToolInputError, ToolNotFoundError


_LOGGER = get_logger(name = "toolcore", level = "INFO")


# Buckets in SECONDS, sized for tool calls (not the proxy's LLM-scale buckets): a schema
# check is sub-millisecond, a RAG+rerank handler is hundreds of ms to a few seconds.
_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

# toolcore_ prefix parallels LLMGuard's llmguard_. Same label set on both so a counter
# and its timing line up.
TOOL_CALLS_TOTAL = Counter(
    "toolcore_tool_calls_total",
    "Tool Core calls by tool, surface (internal|mcp), and outcome.",
    ["tool", "surface", "outcome"],
)
TOOL_CALL_DURATION_SECONDS = Histogram(
    "toolcore_tool_call_duration_seconds",
    "Wall-clock duration of a whole call_tool invocation.",
    ["tool", "surface", "outcome"],
    buckets = _DURATION_BUCKETS,
)


def classify_outcome(exc: Optional[BaseException]) -> str:
    """
    Map the exception that left `call_tool` (or None on success) to an outcome label.
    ToolNotFoundError and PrincipalRequiredError both subclass ToolInputError, so the
    subclasses MUST be checked before their base.

    Taxonomy (shared with the MCP surface): success | not_found | auth_error |
    input_error | upstream_error.
    """
    if exc is None:
        return "success"
    if isinstance(exc, ToolNotFoundError):
        return "not_found"
    if isinstance(exc, PrincipalRequiredError):
        return "auth_error"
    if isinstance(exc, ToolInputError):
        return "input_error"
    # Anything else escaping a handler is an upstream fault (RAG/LLM/supporter failure).
    return "upstream_error"


def record_tool_call(
        tool: str, surface: str, outcome: str, duration_s: float) -> None:
    """Record one completed call: increment the counter, observe the histogram, log JSON."""
    labels = {"tool": tool, "surface": surface, "outcome": outcome}
    TOOL_CALLS_TOTAL.labels(**labels).inc()                                                     # Increment the call counter for this label set
    TOOL_CALL_DURATION_SECONDS.labels(**labels).observe(duration_s)                             # Record the call duration for latency histograms (p50/p95/p99)

    line = orjson.dumps({
        "event": "tool_call",
        "tool": tool,
        "surface": surface,
        "outcome": outcome,
        "duration_ms": round(duration_s * 1000.0, 3),
    }).decode()
    if outcome == "success":
        _LOGGER.info(line)
    else:
        _LOGGER.warning(line)
