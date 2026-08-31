"""
Central resolver for Vertex AI configuration.

Why this exists
---------------
All chat-completion traffic from the chatbot graph now runs on Google Vertex
AI. Vertex needs a GCP ``project`` and ``location`` at client construction  time

Configuration
-------------
- ``GOOGLE_CLOUD_PROJECT``  — GCP project id (required for Vertex).
- ``GOOGLE_CLOUD_LOCATION`` — region, defaults to ``us-central1``.

Authentication uses Application Default Credentials (ADC): a service-account
key referenced by ``GOOGLE_APPLICATION_CREDENTIALS``, or the ambient GCP
identity. No API key is passed for Vertex.

NOTE on CrewAI: ``crewai.LLM`` uses litellm under the hood, which reaches
Vertex with the ``vertex_ai/<model>`` prefix and reads the same
``GOOGLE_CLOUD_PROJECT`` / ``GOOGLE_CLOUD_LOCATION`` env vars.

Routing through LLMGuard
------------------------
``build_chat_model`` reaches Vertex through LLMGuard. Both variables are
**required and must be non-empty** — there is no default and no fallback, because
either would pick an upstream path on the operator's behalf.

- ``LLM_GATEWAY_URL``      — e.g. ``http://la-llmguard:8081``.
- ``LLM_GATEWAY_PROVIDER`` — which upstream LLMGuard routes to, e.g. ``vertex``.

``ChatVertexAI`` remains the client for everything else in the graph: the gateway
serves plain completions only, so nodes using ``with_structured_output`` still
call Vertex directly.
"""

import os

from langchain_core.language_models.chat_models import BaseChatModel


def get_vertex_project() -> str | None:
    """GCP project id for Vertex AI (from ``GOOGLE_CLOUD_PROJECT``)."""
    return os.getenv("GOOGLE_CLOUD_PROJECT")


def get_vertex_location() -> str:
    """Vertex AI region (from ``GOOGLE_CLOUD_LOCATION``; defaults us-central1)."""
    return os.getenv("GOOGLE_CLOUD_LOCATION")


def _require_env(name: str, hint: str) -> str:
    """Read a required env var, or fail loudly.

    Absent and empty are both refused. A default here would pick an upstream path
    on the operator's behalf, and an empty value is far more often a mis-set
    variable than a deliberate choice.
    """
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required and must not be empty. {hint}")
    return value


def get_gateway_url() -> str:
    """LLMGuard base URL. Required, non-empty."""
    return _require_env(
        "LLM_GATEWAY_URL",
        "Set it to LLMGuard's base URL, e.g. http://la-llmguard:8081.",
    )


def build_chat_model(model: str, temperature: float) -> BaseChatModel:
    """A chat model that reaches Vertex through LLMGuard.

    Vertex is still the upstream; the gateway is what adds retry, circuit breaking
    and the rate limit in front of it. The wire format is OpenAI's, so the client
    is ``ChatOpenAI`` and LLMGuard holds the credential.

    Both env vars are required and must be non-empty, and there is no runtime
    fallback: if the gateway is failing, requests fail rather than silently
    bypassing the protections they were routed through. Going back to a direct
    Vertex client is a code change, not a variable.

    Only plain completions may use this — ``with_structured_output`` and tool
    calling are refused with a 400, by design (the gateway proxies user->model
    only).
    """
    base = get_gateway_url()

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        temperature=temperature,
        base_url=base.rstrip("/") + "/v1",
        # LLMGuard substitutes its own upstream credential and ignores this, but
        # the client refuses to construct without one.
        api_key="unused-llmguard-holds-the-key",
        # The gateway is the only retry layer. Retrying here would multiply
        # attempts against the upstream and, worse, retry the gateway's own 429
        # backpressure — which is the signal telling us to stop.
        max_retries=0,
        # Required, like the URL: LLMGuard refuses a request that does not name
        # its upstream (one model can be served by several), so guessing here
        # would only move the 400 further from whoever set the variables.
        extra_body={"provider": _require_env(
            "LLM_GATEWAY_PROVIDER",
            "Name the upstream in LLMGuard's config.yaml, e.g. vertex.",
        )},
    )
