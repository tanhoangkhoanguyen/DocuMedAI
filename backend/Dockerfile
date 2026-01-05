FROM python:3.12-slim

WORKDIR /backend

COPY /backend/requirements.txt /backend

RUN apt-get update && apt-get install -y --no-install-recommends git && \
    pip install --no-cache-dir -r requirements.txt && \
    python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" && \
    rm -rf /root/.cache/pip && \
    rm -rf /root/.cache/huggingface && \
    apt-get purge -y git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY /backend /backend

CMD ["bash"]