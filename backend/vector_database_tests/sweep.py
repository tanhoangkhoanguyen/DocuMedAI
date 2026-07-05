"""
Equal-recall latency sweep (ann-benchmarks methodology).

For the selected DB, sweep the query-time effort knob (HNSW ef / Vespa targetHits), measuring
recall@k and serial latency at each value, then pick the LOWEST-latency config that still
reaches the recall target. The headline latency for each DB is then reported AT EQUAL RECALL,
so a DB can't look fast merely by searching fewer candidates.

Run:  BENCH_DB=qdrant python -m vector_database_tests.sweep --k 10 --recall-target 0.95
"""
from logger import get_logger
from vector_database_tests.utils import registry
from vector_database_tests.recall import evaluate_db

import os, json, argparse

_LOGGER = get_logger(
    name = "vectordb_lab_sweep",
    level = "INFO",
)
RESULTS_DIR = "vector_database_tests/sweep_results"

# Query-time effort grids. ef-style DBs share a grid; Vespa's targetHits is the candidate
# pool (semantically similar role). Grids should bracket the recall target from below.
_EF_GRID = [16, 32, 64, 128, 256]
GRIDS = {
    "qdrant":   _EF_GRID,
    "chromadb": _EF_GRID,
    "milvus":   [64, 128, 256],
    "vespa":    [64, 128, 256],   # targetHits must be >= TOP_K (50)
    # Weaviate ef is class-level (set at create time) -> cannot be swept without recreating
    # the class and re-uploading 1M vectors. Measured at its build-time ef only (single point).
    "weaviate": [None],
}


def _apply_chroma_ef(db: str, collection: str, ef):
    """Chroma's ef is a collection property; set it without re-uploading."""
    if db == "chromadb" and ef is not None:
        registry.get_client(db).set_search_ef(collection, ef)


def run_sweep(db: str, k: int = 10, recall_target: float = 0.95) -> dict:
    collection = registry.normalize_collection(db, registry.DEFAULT_COLLECTION)
    grid = GRIDS.get(db, _EF_GRID)
    trials = []

    for ef in grid:
        _apply_chroma_ef(db, collection, ef)
        res = evaluate_db(db, collection = collection, k = k, search_param = ef)
        _LOGGER.info(
            f"[{db}] ef/targetHits={ef}: recall@{k}={res['recall_at_k']} "
            f"median={res['median_ms']}ms p95={res['p95_ms']}ms"
        )
        trials.append(res)

    # Among configs meeting the target, choose the one with the lowest median latency.
    passing = [t for t in trials if t["recall_at_k"] >= recall_target and t["median_ms"] is not None]
    flagged = False
    if passing:
        chosen = min(passing, key = lambda t: t["median_ms"])
    else:
        # Nobody hit the target: report the highest-recall config and FLAG it.
        flagged = True
        chosen = max(trials, key = lambda t: t["recall_at_k"])
        _LOGGER.warning(
            f"[{db}] No config reached recall@{k} >= {recall_target}. "
            f"Reporting best-recall config (recall={chosen['recall_at_k']}) and flagging."
        )

    return {
        "db": db,
        "k": k,
        "recall_target": recall_target,
        "recall_target_met": not flagged,
        "chosen": chosen,
        "trials": trials,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description = "Equal-recall latency sweep for one DB")
    p.add_argument("--db", default = None)
    p.add_argument("--k", type = int, default = 10)
    p.add_argument("--recall-target", type = float, default = 0.95)
    args = p.parse_args()

    db = registry.resolve_db(args.db)
    result = run_sweep(db, k = args.k, recall_target = args.recall_target)

    os.makedirs(RESULTS_DIR, exist_ok = True)
    out_path = f"{RESULTS_DIR}/{db}.json"
    with open(out_path, "w", encoding = "utf-8") as f:
        json.dump(result, f, indent = 2)
    _LOGGER.info(f"Wrote sweep result -> {out_path}")
    print(json.dumps(result["chosen"], indent = 2))
