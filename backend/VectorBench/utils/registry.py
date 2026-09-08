"""
Single source of truth for selecting which vector DB a benchmark run targets.

Every benchmark entrypoint (data_uploading, sweep, throughput, recall) picks the DB
the same way: CLI `--db` if given, else the `BENCH_DB` env var.
"""
import os
import random
from typing import List

from VectorBench.utils.chromadb_client import get_chromadb_client
from VectorBench.utils.milvus_client import get_milvus_client
from utils.qdrant_client import get_qdrant_client
from VectorBench.utils.vespa_client import get_vespa_client
from VectorBench.utils.weaviate_client import get_weaviate_client

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384

# Top-k is fixed across every DB so the comparison is apples-to-apples.
TOP_K = 50

# Fixed seed for query-order shuffling. Every measurement (recall, sweep, throughput) issues
# queries in a SHUFFLED order rather than file order so that no engine's query-result cache can
# bias its latency by replaying an identical sequence. The seed is fixed so runs reproduce.
SEED = 42


def shuffled_order(n: int, seed: int = SEED) -> List[int]:
    """Deterministic shuffled list of indices [0, n). Used to de-bias result caches without
    losing reproducibility. Callers keep each item's original index (e.g. query_id) so that
    ground-truth alignment is preserved."""
    order = list(range(n))
    random.Random(seed).shuffle(order)
    return order

# Databases that count toward the performance ranking. All are self-hostable and compared under equal resource budgets.
PERF_DBS: List[str] = ["qdrant", "milvus", "weaviate", "chromadb", "vespa"]

# All selectable DBs.
SUPPORTED: List[str] = PERF_DBS

_FACTORIES = {
    "qdrant": get_qdrant_client,
    "milvus": get_milvus_client,
    "weaviate": get_weaviate_client,
    "chromadb": get_chromadb_client,
    "vespa": get_vespa_client,
}

# Weaviate class names must be UpperCamelCase; the rest accept the lowercase collection
# name as-is. Callers pass one logical collection name and let each client normalize.
DEFAULT_COLLECTION = "latency_test"
_WEAVIATE_COLLECTION = "LatencyTest"


def normalize_collection(db: str, collection: str) -> str:
    """Return the collection/class name in the form the given DB requires."""
    if db == "weaviate":
        # Weaviate rejects snake_case class names; map the shared default to its class form.
        return _WEAVIATE_COLLECTION if collection == DEFAULT_COLLECTION else collection
    return collection


def resolve_db(cli_db: str = None) -> str:
    """CLI flag wins, then BENCH_DB env var. Raises if neither is a supported DB."""
    db = (cli_db or os.environ.get("BENCH_DB") or "").strip().lower()
    if db not in SUPPORTED:
        raise ValueError(
            f"No vector DB selected. Pass --db or set BENCH_DB to one of {SUPPORTED} "
            f"(got {db!r})."
        )
    return db


def get_client(
        db: str,
        embedding_model: str = EMBEDDING_MODEL,
        embedding_dimension: int = EMBEDDING_DIMENSION,
    ):
    """Build (or fetch the cached) client for `db`."""
    if db not in _FACTORIES:
        raise ValueError(f"Unknown vector DB {db!r}; expected one of {list(_FACTORIES)}")
    return _FACTORIES[db](
        embedding_model = embedding_model,
        embedding_dimension = embedding_dimension,
    )
