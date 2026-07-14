# syntax=docker/dockerfile:1

FROM python:3.10-slim-bookworm AS builder
WORKDIR /build

# Disable cache
ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements-prod.txt .

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && python -m venv /opt/venv \
    && rm -rf /var/lib/apt/lists/*

# Add virtual environment binaries
ENV PATH="/opt/venv/bin:$PATH"

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
    && pip install -r requirements-prod.txt

FROM python:3.10-slim-bookworm AS runner
WORKDIR /backend

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /models

# Enable logs
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TOKENIZERS_PARALLELISM=false \
    HF_HOME=/models \
    TRANSFORMERS_CACHE=/models

COPY --from=builder /opt/venv /opt/venv

# Pre-cache the embedding model into /models (= HF_HOME) at build time so runtime
# never downloads it. The download is authenticated via a REQUIRED HF_TOKEN build
# secret (higher rate limits, avoids the anonymous throttling that stalls builds).
# Hardening:
#   - HF_TOKEN (required BuildKit secret) authenticates the download. Passed as a
#     secret so it never lands in an image layer.
#     Build with:  docker build --secret id=hf_token,env=HF_TOKEN ...
#   - HF_HUB_DOWNLOAD_TIMEOUT makes a stalled connection fail fast instead of hanging.
#   - the retry loop rides out transient 429s / timeouts.
ENV HF_HUB_DOWNLOAD_TIMEOUT=30
RUN --mount=type=secret,id=hf_token,required=true \
    HF_TOKEN="$(cat /run/secrets/hf_token)"; export HF_TOKEN; \
    if [ -z "$HF_TOKEN" ]; then echo "ERROR: hf_token secret is empty; set HF_TOKEN" >&2; exit 1; fi; \
    for i in 1 2 3 4 5; do \
      python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')" && break; \
      echo "HF model download attempt $i failed; retrying in $((i*10))s..."; \
      sleep $((i*10)); \
    done; \
    python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# Entrypoint: Wait for the Qdrant data upload (from prod_dataset/) to complete, then start the API
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

CMD ["/usr/local/bin/entrypoint.sh"]