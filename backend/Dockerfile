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
    && groupadd --system --gid 1001 group_1 \
    && useradd --system --uid 1001 --gid group_1 --home-dir /backend user_1 \
    && mkdir -p /models \
    && chown user_1:group_1 /models \
    && chown user_1:group_1 /backend

# Enable logs
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TOKENIZERS_PARALLELISM=false \
    HF_HOME=/models \
    TRANSFORMERS_CACHE=/models

COPY --from=builder /opt/venv /opt/venv

COPY --chown=user_1:group_1 . .

USER user_1

CMD ["python", "-m", "services.app.run_app"]