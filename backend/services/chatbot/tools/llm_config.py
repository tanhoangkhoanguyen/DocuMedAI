"""
Central resolver for the LLM proxy base URL.

Why this exists
---------------
All chat-completion traffic from the chatbot graph is routed through the
Go LLM proxy (``backend/llm-proxy/``) instead of hitting the LLM provider
directly. The proxy adds rate limiting, retry/backoff, circuit breaking and
dedup so a burst of chat-completion calls never trips the provider's 429/5xx
limits.

To point a client at the proxy we only need to change its ``base_url`` — the
OpenAI request/response shape is unchanged. Rather than thread a ``base_url``
argument through ``build_graph`` → ``GraphBuilder`` → every node/tool ctor, we
keep ONE resolver here and call it explicitly at each construction site
(``ChatOpenAI(base_url=get_llm_base_url())`` and ``crewai.LLM(base_url=...)``).

That keeps the wiring explicit at the call site (you can see where each client
points) while having a single source of truth for the URL.

Configuration
-------------
``LLM_PROXY_BASE_URL`` env var (set in docker-compose for ``la-backend``).
Defaults to the in-network proxy address. Set it to ``""`` to disable the proxy
and talk to OpenAI directly (langchain/crewai then fall back to their defaults).

NOTE on CrewAI: ``crewai.LLM`` uses litellm under the hood, which does NOT read
``OPENAI_BASE_URL`` reliably — so we MUST pass ``base_url`` to it explicitly.
``langchain_openai.ChatOpenAI`` does read ``OPENAI_BASE_URL``, but we pass it
explicitly anyway so both libraries behave identically and the intent is obvious.
"""

# Default points at the proxy service on the docker network (service name :port).
_DEFAULT_PROXY_BASE_URL = "http://la-llm-proxy:8081/v1"


def get_llm_base_url() -> str | None:
    """
    Return the base_url to hand to ChatOpenAI / crewai.LLM.

    - Unset env  → default proxy URL (normal docker-compose case).
    - Set to ""  → None, meaning "use the library default" (direct OpenAI).
    - Set value  → that value (e.g. a local stub when testing).
    """
    return _DEFAULT_PROXY_BASE_URL