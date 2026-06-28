# Vector Database Latency Benchmark

Benchmarks vector-database **indexing speed** and **retrieval latency at equal recall** to decide which engine to **self-host** for DocuMedAI. Tested engines:

* Qdrant
* Milvus
* Weaviate
* Vespa
* ChromaDB

> **Deployment target: self-hosted (Docker/VM).** All engines were benchmarked on a single machine with equal resource limits (`8 GB RAM`, `4 CPUs`) per container. Results represent self-hosted performance only and should **not** be considered representative of managed cloud offerings. Re-benchmark on the target cloud platform before deployment.

## Why recall is measured (and why speed alone is misleading)

HNSW is an **approximate** index: it trades recall for speed via a query-time effort knob
(`ef` / Vespa `targetHits`). A database can look fast simply because its default knob searches
fewer candidates and returns lower-quality neighbors. **Latency is only comparable at equal
recall.** This benchmark therefore:

1. Computes **exact-kNN ground truth** (brute-force cosine over the full corpus) once.
2. Sweeps each engine's query-effort knob, measuring **recall@10** and serial latency.
3. Picks, per engine, the **lowest-latency config that reaches recall@10 ≥ 0.95**.
4. Reports indexing time + latency **at that equal-recall config**.

This is the standard [ann-benchmarks](https://github.com/erikbern/ann-benchmarks)
methodology.

## Experiment Setup

### Hardware & environment
Same machine, one container per engine, equal `mem_limit`/`cpus`. All engines use the
`sentence-transformers/all-MiniLM-L6-v2` embedding model (**384-dim**), cosine distance, and
identical **build-side** HNSW params:

```
M = 64
efConstruction = 200
```

Query-side effort (`ef` / `targetHits`) is **tuned per engine by the sweep** to reach equal
recall — it is no longer hardcoded.

### Dataset
| Property         | Value             |
| ---------------- | ----------------- |
| Total vectors    | 1,031,434         |
| Vector dimension | 384               |
| Query set size   | 10,000 queries    |
| Distance metric  | Cosine            |

Corpus record: `{id (uuid str), title, split_text, embedded_test:[384]}`.
Query record: `{query, embedded_query:[384], answer}`. `query_id` is the line index over
sorted query files (so ground truth, recall, and retrieval all align).

## Pinned versions (this benchmark run)

Engines and clients are pinned for reproducibility — bump deliberately and re-run, since versions materially affect HNSW performance.

Note: **Weaviate is pinned to client v3**. Client v4 contains potential risk.

| Component        | Image / pin            |
| ---------------- | ---------------------- |
| Qdrant           | `qdrant/qdrant:v1.18.2` · `qdrant-client==1.18.0` |
| Milvus           | `milvusdb/milvus:v2.6.18` · `pymilvus==2.6.15` |
| Weaviate         | `semitechnologies/weaviate:1.37.9` · `weaviate-client==3.26.7` (v3) |
| Vespa            | `vespaengine/vespa:8.709.19` |
| ChromaDB         | `chromadb/chroma:1.5.9` · `chromadb==1.5.9` (HttpClient) |
| Ground truth     | `faiss-cpu==1.14.3` |

## Running the benchmark

All commands run **inside the `la-backend` container** (it has the clients, the embedding model, and the dataset mount). DB selection is via `BENCH_DB` (or `--db`); each step writes a per-DB JSON result so every number records which engine produced it.

```bash
# 0. Bring up the lab stack with equal resource limits
docker compose --profile vectordb-lab up -d --build --wait la-qdrant la-chroma la-weaviate la-milvus la-vespa
docker compose --profile vectordb-lab up -d la-backend
docker compose exec la-backend pip install -r /backend/requirements-dev.txt

# Vespa only: deploy the application package once (adds the doc_id field)
docker compose exec la-vespa vespa deploy --wait 300 /app

# 1. Exact-kNN ground truth — ONCE (depends only on corpus+queries+cosine)
docker compose exec -d la-backend sh -c "cd /backend && python -m vector_database_tests.ground_truth --top-k 100 2>> /backend/logs/gt_stderr.log"

docker compose exec la-backend tail -f /backend/logs/vectordb_lab_ground_truth_20260626.log

# 2. Per engine: upload → sweep (equal-recall config) → open-loop throughput
#    If upload exceeds its 1h budget, it exits non-zero and writes {"timed_out": true}; the `|| continue` then skips sweep + throughput for that engine and moves to the next.
$databases = "qdrant", "milvus", "weaviate", "chromadb", "vespa"
foreach ($db in $databases) {
  docker compose exec -e BENCH_DB=$db -w /backend la-backend `
    python -m vector_database_tests.data_uploading
  if (-not $?) { continue }
  docker compose exec -e BENCH_DB=$db -w /backend la-backend `
    python -m vector_database_tests.sweep --k 10 --recall-target 0.95
  docker compose exec -e BENCH_DB=$db -w /backend la-backend `
    python -m vector_database_tests.throughput --qps 50 100 200 400 800 --duration 30
}
```

Results are written to:
`upload_results/{db}.json`, `sweep_results/{db}.json`, `throughput_results/{db}.json`.

### Why open-loop throughput (not Locust)
Throughput is measured with a **fixed-QPS open-loop** driver (`throughput.py`), not Locust.

Locust is *closed-loop* — each user waits for its response before the next request — so it **cannot hold a target request rate**: when an engine slows, the offered load slows with it, the backlog never forms, and tail latency is hidden (**coordinated omission**). 

The open-loop driver issues requests on a fixed schedule regardless of when responses return, measures each request's latency from its **scheduled** send time, and sweeps a QPS ladder; a struggling engine shows up as **rising latency** (correct) rather than reduced load.

### Cache fairness
Every measurement (`recall.py`, `sweep.py`, `throughput.py`) issues queries in a **shuffled order** (fixed seed, `registry.shuffled_order`) and discards a warmup window. This prevents any engine that caches query *results* from looking fast by replaying an identical query sequence.

The shuffle preserves each query's `query_id`, so ground-truth alignment (and therefore recall)
is unchanged — only the latency is de-biased. The fixed seed keeps runs reproducible.

### Sanity checks before trusting results
* Ground-truth line count == query count.
* Every chosen config reaches recall@10 ≥ 0.95 (`sweep_results/*.json` → `recall_target_met:true`); otherwise it is **flagged** and the best-recall config is reported instead.
* Returned ids overlap ground-truth ids (catches any id-extraction regression).

## Results

Full run over the 1M-vector corpus (999,559 vectors indexed; 10,000 queries). All numbers are transcribed from the per-DB JSON in `upload_results/`, `sweep_results/`, `throughput_results/`.

> **ChromaDB - DNF.** Its upload hit the 1-hour indexing budget at 145k/1M vectors
> (`upload_results/chromadb.json` → `"timed_out": true`) and was skipped, so it has no sweep or
> throughput data and is excluded from the comparison.

### Indexing time (1M vectors)
| Database | Indexing time | Notes |
| -------- | ------------------- | ----- |
| Qdrant   | 315.6s | fastest |
| Milvus   | 615.2s | includes explicit index build |
| Vespa    | 2130.7s | |
| Weaviate | 2355.5s | |
| ChromaDB | DNF | timed out at 1h (145k/1M indexed) |

### Serial latency at recall@10 ≥ 0.95 (from sweep)
Each row is the **lowest-median-latency config that still reaches recall@10 ≥ 0.95** — the
ann-benchmarks selection rule (`sweep.py` `chosen`).

| Database | recall@10 | search_param | median (ms) | p95 (ms) |
| -------- | --------- | ------------ | ----------- | -------- |
| Qdrant   | 0.9608 | ef=32 | 4.383 | 6.201 |
| Milvus   | 0.9769 | ef=64 | 6.316 | 10.194 |
| Vespa    | 0.9737 | targetHits=64 | 7.749 | 10.659 |
| Weaviate | 0.9626 | (class-level ef) | 7.205 | 24.321 |
| ChromaDB | DNF | — | — | — |

> **Methodology note - `ef` ≥ `top_k`.** We retrieve `TOP_K = 50` per query. **Milvus and Vespa reject** a query-effort value below `top_k`

### Open-loop throughput at the equal-recall config
Open-loop fixed-QPS driver (`throughput.py`), 30s per rung. Every engine **holds the target rate through 200 QPS** (achieved ≈ target, bounded tail), then **collapses at 400 QPS**
(achieved_rps falls to 250–330 and latency rises into seconds - correct open-loop backpressure,
not a hung loop). The 200-QPS rung below is therefore each engine's highest sustained rate.
Full per-rung ladders in `throughput_results/{db}.json`.

| Database | sustained QPS | median (ms) | p95 (ms) | p99 (ms) |
| -------- | ------------- | ----------- | -------- | -------- |
| Qdrant   | 200 | 4.109 | 8.344 | 10.109 |
| Milvus   | 200 | 8.305 | 55.535 | 93.035 |
| Vespa    | 200 | 8.826 | 21.039 | 70.127 |
| Weaviate | 200 | 7.468 | 339.906 | 406.034 |
| ChromaDB | DNF | — | — | — |

### Conclusion
**Qdrant wins on all three axes** - fastest to index, lowest serial latency at equal recall, and the tightest tail at 200 QPS.

## Limitations
1. Single-machine, single-node; no distributed/sharded scaling tested.
2. Self-host scope only - cloud tiers must be re-validated separately.
3. 1M-vector scale.
4. **Weaviate query `ef` is class-level** (v3): it cannot be swept without recreating the
   class and re-uploading, so Weaviate is measured at its build-time `ef` (single point), not
   swept like the others. Note this when comparing.

## Folder structure
```
vector_database_tests/
├── dataset/                # 1M-vector corpus (jsonl)
├── generated_queries/      # query set (jsonl)
├── ground_truth/           # exact-kNN ground truth (generated)
├── upload_results/         # per-DB indexing time (generated)
├── sweep_results/          # per-DB equal-recall config (generated)
├── throughput_results/     # per-DB open-loop throughput (generated)
│
├── data_processing.py      # builds the dataset
├── data_uploading.py       # indexing benchmark (+ Milvus index build)
├── ground_truth.py         # exact-kNN ground truth (faiss/numpy)
├── recall.py               # recall@k + serial latency
├── sweep.py                # equal-recall latency sweep
├── throughput.py           # open-loop fixed-QPS throughput
│
└── utils/
    ├── registry.py         # DB selection (BENCH_DB) + shared constants
    ├── vespa_config/
    ├── data_storage/
    ├── chromadb_client.py
    ├── milvus_client.py
    ├── qdrant_client.py
    ├── vespa_client.py
    └── weaviate_client.py
```
