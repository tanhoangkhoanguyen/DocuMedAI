"""
Protocol-level metrics for the MCP server — a different axis from the Tool Core's per-tool
metrics (toolcore/observability.py). This counts JSON-RPC *methods* (initialize / tools/list
/ tools/call) and their outcome, so protocol success rates are visible independent of which
tool ran.

`mcp_` prefix mirrors the Go proxy's `llmproxy_`; the outcome label set is the shared
taxonomy (success | input_error | auth_error | upstream_error | not_found).
"""
from prometheus_client import Counter


MCP_REQUESTS_TOTAL = Counter(
    "mcp_requests_total",
    "MCP JSON-RPC requests by method and outcome.",
    ["method", "outcome"],
)


def record_mcp_request(method: str, outcome: str) -> None:
    """Count one completed MCP request. `method` is the JSON-RPC method (or 'unknown')."""
    MCP_REQUESTS_TOTAL.labels(method = method, outcome = outcome).inc()
