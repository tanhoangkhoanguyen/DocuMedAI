"""
arq worker for asynchronous document ingestion.

Runs as its own process (`arq services.documents_upload.worker.WorkerSettings`) in the
la-doc-worker container. The upload API writes the file bytes to a shared bind
mount and enqueues an `ingest_task` job (deduped by _job_id=doc_id). This worker
parses/chunks/embeds/stores the document and updates its status in MongoDB.

Job status lifecycle (persisted in Mongo `documents.status`):
    queued -> processing -> ready        (success)
                         -> failed        (after arq exhausts max_tries)
"""
import asyncio, os
from dotenv import load_dotenv
load_dotenv()

from arq.connections import RedisSettings

from logger import get_logger
from services.utils.mongo_client import get_mongo_client
from services.documents_upload.ingest import ingest_document

_LOGGER = get_logger(name = "doc_worker", level = "INFO")

# Separate Redis logical DB (2) so job keys never collide with the app cache
# (DB 0) or the LLM-proxy (DB 1). Host/port come from the compose network.
_REDIS_HOST = os.getenv("REDIS_HOST", "la-redis")
_REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
_REDIS_DB = int(os.getenv("ARQ_REDIS_DB", "2"))

REDIS_SETTINGS = RedisSettings(host = _REDIS_HOST, port = _REDIS_PORT, database = _REDIS_DB)


_MAX_TRIES = 3


async def ingest_task(
        ctx,
        user_id: str,
        doc_id: str,
        filename: str,
        mime: str,
        storage_path: str,
    ) -> int:
    """
    arq task: ingest one uploaded document.

    On failure it re-raises so arq retries (up to max_tries). Only on the FINAL
    attempt does it mark the document 'failed' — earlier failures stay 'processing'
    because a retry may still succeed. `job_try` is supplied by arq in ctx.
    """
    mongo = get_mongo_client()
    job_try = ctx.get("job_try", 1)
    max_tries = ctx.get("max_tries", _MAX_TRIES)

    try:
        mongo.set_document_status(doc_id, "processing", error = None)

        with open(storage_path, "rb") as f:
            raw = f.read()

        loop = asyncio.get_running_loop()
        # ingest_document is CPU/IO-bound (embeddings) — run off the event loop.
        chunk_count = await loop.run_in_executor(
            None, ingest_document, user_id, doc_id, filename, mime, raw,
        )

        mongo.set_document_status(doc_id, "ready", chunk_count = chunk_count, error = None)
        try:
            os.remove(storage_path)                # success: drop the temp upload
        except OSError:
            pass
        _LOGGER.info(f"ingest ok doc_id={doc_id} chunks={chunk_count}")
        return chunk_count

    except Exception as exc:
        if job_try >= max_tries:                   # exhausted retries -> terminal
            mongo.set_document_status(doc_id, "failed", error = str(exc)[:500])
            _LOGGER.error(f"ingest failed permanently doc_id={doc_id}: {exc}")
        else:
            _LOGGER.error(f"ingest attempt {job_try}/{max_tries} failed doc_id={doc_id}: {exc}")
        raise                                      # let arq retry / record the failure


class WorkerSettings:
    functions = [ingest_task]
    redis_settings = REDIS_SETTINGS
    max_tries = _MAX_TRIES
    job_timeout = 300
    keep_result = 3600
