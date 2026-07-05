"""
Production seeder for the MedicalTerms Qdrant collection.

Runs once at backend startup (via entrypoint.sh) to guarantee the medical-terms
knowledge base is present before the API serves traffic.

Behaviour (idempotent):
  - If the MedicalTerms collection already exists AND is non-empty -> do nothing.
  - Otherwise, (re)create it and upload every *.jsonl file in prod_dataset/.

STRICT pre-embedded format. Each JSONL line MUST be an object with:
    id            : stable unique id (str/int)
    split_text    : the text stored as payload {"query": ...}
    embedded_test : the precomputed embedding vector (list[float], dim 384)
Records missing any field, or whose vector dimension differs from the configured
embedding dimension, are rejected (the seeder raises). Vectors are NOT computed
here — pre-embed offline with all-MiniLM-L6-v2 (dim 384). See README.md.

Run standalone:  python -m services.chatbot.seed_prod_data
"""
import os, json

from logger import get_logger
from vector_database_tests.utils.qdrant_client import get_qdrant_client


_LOGGER = get_logger(
    name = "prod_data_seeder", 
    level = "INFO"
)

_MEDICAL_COLLECTION = "MedicalTerms"
PROD_DATASET_DIR = "services/chatbot/prod_dataset"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384
BATCH_SIZE = 1000

_REQUIRED_FIELDS = ("id", "split_text", "embedded_test")


def _dataset_files(folder_path: str) -> list[str]:
    if not os.path.isdir(folder_path):
        return []
    return [
        f"{folder_path}/{name}"
        for name in sorted(os.listdir(folder_path))
        if name.endswith(".jsonl")
    ]


def _validate_record(record: dict, source: str, line_no: int) -> None:
    missing = [f for f in _REQUIRED_FIELDS if f not in record]
    if missing:
        raise ValueError(
            f"{source}:{line_no} — record missing required field(s) {missing}. "
            f"prod_dataset requires the strict pre-embedded format "
            f"{_REQUIRED_FIELDS}. See services/chatbot/README.md."
        )
    vector = record["embedded_test"]
    if not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSION:
        raise ValueError(
            f"{source}:{line_no} — 'embedded_test' must be a length-{EMBEDDING_DIMENSION} "
            f"vector (got {type(vector).__name__} of length "
            f"{len(vector) if isinstance(vector, list) else 'n/a'}). "
            f"Pre-embed with {EMBEDDING_MODEL}."
        )


def seed(force: bool = False) -> dict:
    """
    Ensure MedicalTerms is populated from prod_dataset/.

    Returns a small summary dict. `force=True` re-uploads even if the collection
    is already populated (wipes and rebuilds).
    """
    qdrant_client = get_qdrant_client(
        embedding_model = EMBEDDING_MODEL,
        embedding_dimension = EMBEDDING_DIMENSION,
    )

    existing = qdrant_client.count_points(_MEDICAL_COLLECTION)
    if existing > 0 and not force:
        _LOGGER.info(
            f"'{_MEDICAL_COLLECTION}' already populated ({existing} points) — skipping seed."
        )
        return {"collection": _MEDICAL_COLLECTION, "seeded": False, "n_vectors": existing}

    files = _dataset_files(PROD_DATASET_DIR)
    if not files:
        _LOGGER.warning(
            f"No .jsonl files found in {PROD_DATASET_DIR} — nothing to seed."
        )
        return {"collection": _MEDICAL_COLLECTION, "seeded": False, "n_vectors": existing}

    # Fresh build: create_collection wipes any partial/stale collection first.
    qdrant_client.create_collection(_MEDICAL_COLLECTION)

    ids, queries, embedded_queries = [], [], []
    n_vectors = 0

    def flush():
        nonlocal ids, queries, embedded_queries, n_vectors
        if not ids:
            return
        qdrant_client.push_documents(_MEDICAL_COLLECTION, ids, queries, embedded_queries)
        n_vectors += len(ids)
        ids, queries, embedded_queries = [], [], []

    for file_path in files:
        _LOGGER.info(f"Seeding from {file_path}")
        with open(file_path, "r", encoding = "utf-8") as f:
            for line_no, line in enumerate(f, start = 1):
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                _validate_record(record, file_path, line_no)
                ids.append(record["id"])
                queries.append(record["split_text"])
                embedded_queries.append(record["embedded_test"])
                if len(ids) == BATCH_SIZE:
                    flush()
    flush()

    _LOGGER.info(f"Seeded '{_MEDICAL_COLLECTION}' with {n_vectors} vectors from {len(files)} file(s).")
    return {
        "collection": _MEDICAL_COLLECTION,
        "seeded": True,
        "n_vectors": n_vectors,
        "files": len(files),
    }


if __name__ == "__main__":
    force = os.getenv("SEED_FORCE", "").lower() in ("1", "true", "yes")
    seed(force = force)
