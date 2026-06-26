"""
Build exact-kNN ground truth for recall@k.

Brute-forces cosine nearest neighbors for every query against the full ~1M corpus, once,
and caches the result. recall.py / sweep.py compare each DB's approximate (HNSW) results
against this to measure how much accuracy the ANN index trades away for speed — the missing
piece that makes the latency comparison meaningful.

The ground truth depends ONLY on (corpus, queries, cosine) — not on any DB — so it is
computed a single time and reused for every DB.

Run:  python -m vector_database_tests.ground_truth --top-k 100
"""
from logger import get_logger

import os, json, argparse, warnings, faiss
import numpy as np
warnings.filterwarnings("ignore")

LOGGER = get_logger(__name__)

DATASET_DIR = "vector_database_tests/dataset"
QUERIES_DIR = "vector_database_tests/generated_queries"
OUT_PATH = "vector_database_tests/ground_truth/ground_truth.jsonl"


def _jsonl_files(folder: str):
    return [os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.endswith(".jsonl")]


def load_corpus(folder: str = DATASET_DIR):
    """Return (ids: list[str], vectors: float32 N x D)."""
    ids, vecs = [], []
    for path in _jsonl_files(folder):
        with open(path, "r", encoding = "utf-8") as f:
            for line in f:
                rec = json.loads(line)
                ids.append(rec["id"])
                vecs.append(rec["embedded_test"])
    return ids, np.asarray(vecs, dtype = np.float32)


def load_queries(folder: str = QUERIES_DIR):
    """Return (query_texts: list[str], vectors: float32 Q x D). query_id == list index."""
    texts, vecs = [], []
    for path in _jsonl_files(folder):
        with open(path, "r", encoding = "utf-8") as f:
            for line in f:
                rec = json.loads(line)
                texts.append(rec.get("query", ""))
                vecs.append(rec["embedded_query"])
    return texts, np.asarray(vecs, dtype = np.float32)


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis = 1, keepdims = True)
    norms[norms == 0] = 1.0
    return mat / norms


def _knn_faiss(corpus: np.ndarray, queries: np.ndarray, top_k: int):
    c = _l2_normalize(corpus.copy())
    q = _l2_normalize(queries.copy())
    index = faiss.IndexFlatIP(c.shape[1])   # inner product on normalized vectors == cosine
    index.add(c)
    _, idx = index.search(q, top_k)
    return idx


def _knn_numpy(corpus: np.ndarray, queries: np.ndarray, top_k: int, chunk: int = 100_000):
    """Chunked fallback if faiss is unavailable. Cosine via normalized dot product."""
    c = _l2_normalize(corpus.copy())
    q = _l2_normalize(queries.copy())
    Q = q.shape[0]
    best_idx = np.zeros((Q, top_k), dtype = np.int64)
    best_sim = np.full((Q, top_k), -np.inf, dtype = np.float32)
    for start in range(0, c.shape[0], chunk):
        block = c[start:start + chunk]              # (B, D)
        sims = q @ block.T                          # (Q, B)
        # merge this block's top-k with the running top-k
        merged_sim = np.concatenate([best_sim, sims], axis = 1)
        merged_idx = np.concatenate(
            [best_idx, np.arange(start, start + block.shape[0])[None, :].repeat(Q, axis = 0)],
            axis = 1,
        )
        part = np.argpartition(-merged_sim, top_k - 1, axis = 1)[:, :top_k]
        best_sim = np.take_along_axis(merged_sim, part, axis = 1)
        best_idx = np.take_along_axis(merged_idx, part, axis = 1)
    # final sort within the kept top-k
    order = np.argsort(-best_sim, axis = 1)
    return np.take_along_axis(best_idx, order, axis = 1)


def build_ground_truth(top_k: int = 100, out_path: str = OUT_PATH, force: bool = False) -> str:
    if os.path.exists(out_path) and not force:
        LOGGER.info(f"Ground truth already exists at {out_path} (use --force to rebuild).")
        return out_path

    LOGGER.info("Loading corpus...")
    corpus_ids, corpus_vecs = load_corpus()
    LOGGER.info(f"Corpus: {corpus_vecs.shape[0]} vectors x {corpus_vecs.shape[1]} dims")

    LOGGER.info("Loading queries...")
    query_texts, query_vecs = load_queries()
    LOGGER.info(f"Queries: {query_vecs.shape[0]}")

    try:
        LOGGER.info("Computing exact kNN with faiss (IndexFlatIP)...")
        nn_idx = _knn_faiss(corpus_vecs, query_vecs, top_k)
    except ImportError:
        LOGGER.warning("faiss not available; falling back to chunked numpy (slower).")
        nn_idx = _knn_numpy(corpus_vecs, query_vecs, top_k)

    os.makedirs(os.path.dirname(out_path), exist_ok = True)
    with open(out_path, "w", encoding = "utf-8") as f:
        for qid in range(nn_idx.shape[0]):
            neighbor_ids = [corpus_ids[i] for i in nn_idx[qid]]
            f.write(json.dumps({
                "query_id": qid,
                "query_text": query_texts[qid],
                "neighbor_ids": neighbor_ids,
            }, ensure_ascii = False) + "\n")
    LOGGER.info(f"Wrote {nn_idx.shape[0]} ground-truth rows (top-{top_k}) -> {out_path}")
    return out_path


if __name__ == "__main__":
    p = argparse.ArgumentParser(description = "Build exact-kNN ground truth for recall@k")
    p.add_argument("--top-k", type = int, default = 100)
    p.add_argument("--out", default = OUT_PATH)
    p.add_argument("--force", action = "store_true")
    args = p.parse_args()
    build_ground_truth(top_k = args.top_k, out_path = args.out, force = args.force)
