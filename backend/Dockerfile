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
# never downloads it. Unauthenticated HF downloads are rate-limited (HTTP 429),
# which otherwise fails CI on every cold run.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

CMD ["python", "-m", "services.app.run_app"]