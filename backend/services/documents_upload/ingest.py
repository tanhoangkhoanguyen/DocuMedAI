"""
Ingest one uploaded document into the UserDocuments Qdrant collection:
extract text -> chunk -> embed -> store, tagged with user_id/doc_id.

Idempotent: point ids are deterministic (uuid5 of "{doc_id}:{chunk_index}") and
every ingest deletes the doc's existing points first, so re-running a job for the
same doc_id never creates duplicate chunks.
"""
from typing import List

from logger import get_logger
from utils.qdrant_client import get_qdrant_client
from services.documents_upload.constants import (
    USER_DOCUMENTS_COLLECTION,
    EMBEDDING_MODEL,
    EMBEDDING_DIMENSION,
    MAX_CHUNKS,
    EMBED_BATCH_SIZE,
)
from services.documents_upload.parsing import extract_text
from services.documents_upload.chunking import chunk_text
from utils.pattern_cipher import get_pattern_cipher

_LOGGER = get_logger(name = "doc_ingest", level = "INFO")


def _chunk_point_id(doc_id: str, chunk_index: int) -> str:
    # Deterministic so re-ingesting the same doc reuses the same point ids
    return get_pattern_cipher().hash_username(f"{doc_id}:{chunk_index}")


def ingest_document(
        user_id: str,
        doc_id: str,
        filename: str,
        mime: str,
        raw: bytes,
    ) -> int:
    """
    Full ingestion for one document. Returns the number of chunks stored.
    Raises on parse/embed/store failure so the worker can retry / mark failed.
    """
    text = extract_text(raw, mime, filename)
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("Document produced no chunks after parsing/splitting")
    if len(chunks) > MAX_CHUNKS:
        _LOGGER.info(f"doc_id={doc_id} capped {len(chunks)} -> {MAX_CHUNKS} chunks")
        chunks = chunks[:MAX_CHUNKS]

    qdrant_client = get_qdrant_client(EMBEDDING_MODEL, EMBEDDING_DIMENSION)
    if not qdrant_client.collection_exists(USER_DOCUMENTS_COLLECTION):
        qdrant_client.create_collection(
            USER_DOCUMENTS_COLLECTION,
            payload_indexes = ["user_id", "doc_id"],
        )
    # Delete-first. Safe re-ingestion (idempotent even if ids ever changed).
    qdrant_client.delete_by_doc(USER_DOCUMENTS_COLLECTION, user_id, doc_id)

    for start in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        ids: List[str] = []
        vectors: List[List[float]] = []
        payloads: List[dict] = []
        for offset, chunk in enumerate(batch):
            idx = start + offset
            ids.append(_chunk_point_id(doc_id, idx))
            vectors.append(qdrant_client.embed_query(chunk))
            payloads.append({
                "user_id": user_id,
                "doc_id": doc_id,
                "source": filename,
                "chunk_index": idx,
                "text": chunk,
            })
        qdrant_client.push_documents(
            USER_DOCUMENTS_COLLECTION,
            ids = ids,
            queries = None,
            embedded_queries = vectors,
            payloads = payloads,
        )

    _LOGGER.info(f"Ingested doc_id={doc_id} user_id={user_id} chunks={len(chunks)}")
    return len(chunks)
