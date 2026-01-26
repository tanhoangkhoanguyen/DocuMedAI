FROM python:3.12-slim

WORKDIR /backend

COPY backend/requirements.txt .

RUN apt-get update && apt-get install -y --no-install-recommends git && \
    pip install --no-cache-dir cython && \
    pip install --no-cache-dir -r requirements.txt && \
    rm -rf /root/.cache/pip /root/.cache/huggingface && \
    apt-get purge -y git && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY backend/ .

CMD ["bash"]