#!/usr/bin/env bash
# Backend container entrypoint.
#   1. Wait for Qdrant to be reachable.
#   2. Seed the MedicalTerms collection from prod_dataset/ (idempotent: skips if
#      the collection is already populated).
#   3. Exec the FastAPI app.
set -euo pipefail

QDRANT_URL="${QDRANT_URL:-http://la-qdrant:6333}"

echo "[entrypoint] waiting for Qdrant at ${QDRANT_URL} ..."
for i in $(seq 1 60); do
  if curl -sf "${QDRANT_URL}/readyz" >/dev/null 2>&1 \
     || curl -sf "${QDRANT_URL}/" >/dev/null 2>&1; then
    echo "[entrypoint] Qdrant is up."
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "[entrypoint] Qdrant not reachable after 60s — continuing anyway; seed may fail." >&2
  fi
  sleep 1
done

echo "[entrypoint] seeding MedicalTerms from prod_dataset/ ..."
python -m services.chatbot.seed_prod_data

echo "[entrypoint] starting API ..."
exec python -m services.app.run_app
