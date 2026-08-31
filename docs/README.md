# Documentation

Cross-cutting docs live here. Anything describing one package stays next to that
package's code — `backend/llmguard/README.md`, `backend/mcp_server/README.md`,
and so on — so it is edited in the same diff as the thing it describes.

| | |
|---|---|
| [deployment.md](deployment.md) | Standing the whole stack up, host prerequisites onward |
| [benchmarks/](benchmarks/) | Measurements and the runbooks that reproduce them |
| [roadmap/](roadmap/) | Plans, per subsystem. Issue content, not status |

## benchmarks/

| | |
|---|---|
| [llmguard-results.md](benchmarks/llmguard-results.md) | Gateway capacity, overhead, breaker, streaming |
| [llmguard-procedure.md](benchmarks/llmguard-procedure.md) | Commands that produced them |
| [mcp-procedure.md](benchmarks/mcp-procedure.md) | Two-VM GCP setup for MCP serving capacity |

Numbers and method are separate files on purpose: results get replaced each run,
the procedure does not.

## roadmap/

[llmguard.md](roadmap/llmguard.md) · [toolcore.md](roadmap/toolcore.md)

These stay as plans — issues are edited to reflect the approach taken, never
marked done.
