FROM python:3.10-slim-bookworm

WORKDIR /backend

COPY backend/requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends git && \
    python -m pip install --no-cache-dir --upgrade "pip==23.3.1" && \
    pip install --no-cache-dir cython && \
    pip install --no-cache-dir -r requirements.txt && \
    rm -rf /root/.cache/pip /root/.cache/huggingface && \
    apt-get purge -y git && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY backend/ .

CMD ["bash"]