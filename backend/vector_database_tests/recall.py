"""
Recall@k measurement for a single DB against the cached exact-kNN ground truth.

`evaluate_db` makes ONE serial pass over all queries and returns BOTH mean recall@k AND the
per-query latency distribution from that same pass, so the sweep can pick the config that
hits a recall target at the lowest latency.

This is serial (one query at a time) on purpose: it isolates pure search latency and recall.
Concurrency/throughput is measured separately by throughput.py (open-loop fixed-QPS).
"""
from logger import get_logger
from vector_database_tests.utils import registry
from vector_database_tests.ground_truth import load_queries, OUT_PATH

import time, json, statistics
import numpy as np

_LOGGER = get_logger(
    name = "vectordb_lab_recall",
    level = "INFO"
)


def load_ground_truth(path: str = OUT_PATH) -> dict:
    """query_id -> list[neighbor_id] (ordered, exact)."""
    truth = {}
    with open(path, "r", encoding = "utf-8") as f:
        for line in f:
            rec = json.loads(line)
            truth[rec["query_id"]] = rec["neighbor_ids"]
    return truth


def recall_at_k(retrieved_ids, truth_ids, k: int) -> float:
    if k <= 0:
        return 0.0
    truth_set = set(str(t) for t in truth_ids[:k])
    if not truth_set:
        return 0.0
    hit = sum(1 for r in (str(x) for x in retrieved_ids[:k]) if r in truth_set)
    return hit / len(truth_set)


def warmup(client, collection: str, query_vecs, n: int = 1000, search_param = None):
    for i in range(min(n, len(query_vecs))):
        try:
            client.retrieve_ids(collection, query_vecs[i].tolist(), registry.TOP_K, search_param)
        except Exception:
            break


def evaluate_db(
        db: str,
        collection: str = None,
        k: int = 10,
        search_param = None,
        ground_truth_path: str = OUT_PATH,
        do_warmup: bool = True,
    ) -> dict:
    """Return {recall@k, median_ms, p95_ms, n_queries} for `db` at `search_param`."""
    collection = collection or registry.normalize_collection(db, registry.DEFAULT_COLLECTION)
    client = registry.get_client(db)
    truth = load_ground_truth(ground_truth_path)
    _, query_vecs = load_queries()   # same sorted order as ground_truth -> query_id == index

    if do_warmup:
        warmup(client, collection, query_vecs, search_param = search_param)

    # Issue queries in a fixed shuffled order to reduce cache-related latency bias.
    # Query IDs remain unchanged, so recall uses the same ground truth and is unaffected.
    order = registry.shuffled_order(len(query_vecs))
    recalls, latencies_ms = [], []
    for qid in order:
        if qid not in truth:
            continue
        vec = query_vecs[qid].tolist()
        start = time.perf_counter()
        retrieved = client.retrieve_ids(collection, vec, registry.TOP_K, search_param)
        latencies_ms.append((time.perf_counter() - start) * 1000.0)
        recalls.append(recall_at_k(retrieved, truth[qid], k))

    if not recalls:
        return {"db": db, "recall_at_k": 0.0, "median_ms": None, "p95_ms": None, "n_queries": 0}

    latencies_ms.sort()
    p95 = latencies_ms[min(len(latencies_ms) - 1, int(0.95 * len(latencies_ms)))]
    return {
        "db": db,
        "k": k,
        "search_param": search_param,
        "recall_at_k": round(float(np.mean(recalls)), 4),
        "median_ms": round(statistics.median(latencies_ms), 3),
        "p95_ms": round(p95, 3),
        "n_queries": len(recalls),
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description = "Measure recall@k for one DB")
    p.add_argument("--db", default = None)
    p.add_argument("--k", type = int, default = 10)
    p.add_argument("--search-param", type = int, default = None)
    args = p.parse_args()
    db = registry.resolve_db(args.db)
    result = evaluate_db(db, k = args.k, search_param = args.search_param)
    _LOGGER.info(f"Recall result: {result}")
    print(json.dumps(result, indent = 2))
