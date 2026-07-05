"""Shared constants for the per-user document upload / ingestion pipeline."""
import os

# Qdrant collection holding ALL users' uploaded-document chunks. Retrieval is
# always filtered by user_id, so this single collection is multi-tenant. It is
# separate from "MedicalTerms" (curated knowledge) which stays untouched.
USER_DOCUMENTS_COLLECTION = "UserDocuments"

# Must match the graph config in run_app.py
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384

# Chunking — mirrors backend/vector_database_tests/data_processing.py
CHUNK_SIZE = 512
CHUNK_OVERLAP = 64

# Upload validation
ALLOWED_MIME = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
}
MAX_DESCRIPTION = 500
MAX_BYTES = 20 * 1024 * 1024        # 20 MB per file
MAX_CHUNKS = 2000                   # cap chunks per document (guards huge files)
EMBED_BATCH_SIZE = 256              # points per Qdrant upload batch

# Shared bind-mounted dir where the API drops uploaded bytes for the worker to read
USER_DOCUMENTS_STORAGE_DIR = "services/utils/data_storage/user_documents_storage"
