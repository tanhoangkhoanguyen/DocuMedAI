"""
Document upload API — one document per user, async ingestion.

POST   /documents    upload (multipart: file + required description); replaces any
                     existing doc for the user. Returns 202 + doc_id; ingestion is async.
GET    /documents    the caller's document + ingestion status (404 if none).
DELETE /documents    delete the caller's document (Qdrant chunks + metadata).

Parse/chunk/embed/store happens in the arq worker. This router validates, persists
bytes to a shared bind mount, records metadata, and enqueues the job. The user's
`description` drives tool routing in the graph (see Agents node), so it is required.
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from typing import Any, Dict

from logger import get_logger
from services.app.auth_deps import get_workspace, require_bearer_claims
from services.app.chatbot_workspace import ChatbotWorkspace
from services.documents_upload.constants import (
    ALLOWED_MIME,
    MAX_BYTES,
    MAX_DESCRIPTION,
    USER_DOCUMENTS_STORAGE_DIR,
)

_LOGGER = get_logger(
    name = "documents_api", 
    level = "INFO"
)


upload_documents_router = APIRouter(tags = ["documents"])


def _storage_path(doc_id: str) -> str:
    os.makedirs(USER_DOCUMENTS_STORAGE_DIR, exist_ok = True)
    return f"{USER_DOCUMENTS_STORAGE_DIR}/{doc_id}"


@upload_documents_router.post("/documents", status_code = 202)
async def upload_document(
        request: Request,
        file: UploadFile = File(...),
        description: str = Form(...),
        claims: Dict[str, Any] = Depends(require_bearer_claims),
        workspace: ChatbotWorkspace = Depends(get_workspace),
    ) -> Dict[str, str]:
    description = (description or "").strip()
    if not description:
        raise HTTPException(status_code = 400, detail = "A document description is required.")
    if len(description) > MAX_DESCRIPTION:
        raise HTTPException(
            status_code = 400,
            detail = f"Description too long (max {MAX_DESCRIPTION} chars).",
        )

    mime = file.content_type or ""
    if mime not in ALLOWED_MIME:
        raise HTTPException(
            status_code = 415,
            detail = f"Unsupported file type '{mime}'. Allowed: PDF, DOCX, TXT.",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code = 400, detail = "Empty file")
    if len(raw) > MAX_BYTES:
        raise HTTPException(
            status_code = 413,
            detail = f"File too large ({len(raw)} bytes, max {MAX_BYTES}).",
        )
    if mime == "application/pdf" and not raw[:5].startswith(b"%PDF"):
        raise HTTPException(status_code = 400, detail = "Not a valid PDF")

    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        raise HTTPException(status_code = 503, detail = "Ingestion queue unavailable")

    doc_id = workspace.new_document_id(claims["id"])
    filename = file.filename or f"{doc_id}.{ALLOWED_MIME[mime]}"

    # Persist bytes for the worker (both share ./backend:/backend).
    path = _storage_path(doc_id)
    with open(path, "wb") as f:
        f.write(raw)

    # Replace semantics: atomically drops the user's previous doc (chunks + metadata)
    # before inserting this one, upholding the 1-doc-per-user rule.
    workspace.prepare_new_document(claims["id"], doc_id, filename, mime, len(raw), description)

    # _job_id=doc_id dedupes concurrent enqueues of the same doc.
    await pool.enqueue_job(
        "ingest_task",
        claims["id"], doc_id, filename, mime, path,
        _job_id = doc_id,
    )

    _LOGGER.info(f"queued ingest doc_id={doc_id} user_id={claims['id']} mime={mime}")
    return {"doc_id": doc_id, "status": "queued", "filename": filename}


@upload_documents_router.get("/documents")
def get_document(
        claims: Dict[str, Any] = Depends(require_bearer_claims),
        workspace: ChatbotWorkspace = Depends(get_workspace),
    ) -> Dict[str, Any]:
    doc = workspace.get_user_document(claims["id"])
    if doc is None:
        raise HTTPException(status_code = 404, detail = "No document uploaded")
    return doc


@upload_documents_router.delete("/documents")
def delete_document(
        claims: Dict[str, Any] = Depends(require_bearer_claims),
        workspace: ChatbotWorkspace = Depends(get_workspace),
    ) -> Dict[str, str]:
    doc = workspace.get_user_document(claims["id"])
    if doc is None:
        raise HTTPException(status_code = 404, detail = "No document uploaded")
    workspace.delete_user_document(claims["id"], doc["doc_id"])
    return {"doc_id": doc["doc_id"]}
