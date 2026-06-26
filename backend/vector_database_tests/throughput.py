"""
Open-loop, fixed-QPS throughput benchmark (replaces the old Locust closed-loop test).

WHY OPEN-LOOP (not Locust):
Locust is *closed-loop* — each simulated user waits for its response before issuing the next
request. Under that model you cannot hold a target request rate: if the engine slows down, the
offered load slows with it, the queue never builds, and tail latency is hidden. This driver reproduces that:

  * A scheduler issues requests at a TARGET arrival rate (QPS), independent of when prior
    responses come back. Each request's latency is measured from its SCHEDULED send time, so a
    backed-up engine shows up as rising latency (correct) rather than reduced load (the lie).
  * A bounded worker pool executes the (synchronous) client calls. If the engine can't keep up,
    the backlog grows and latency climbs — backpressure, not a hung harness.
  * A QPS ladder is swept; the engine's sustainable throughput is the highest rung where it
    keeps up (achieved_rps ~= target and tail latency stays bounded).

Queries are issued in a SHUFFLED order (fixed seed, via registry.shuffled_order) and a warmup
window is discarded, so no engine's query-result cache can bias the numbers. The query path is
exactly registry.get_client(db).retrieve_ids(...), the same call recall.py / sweep.py use.

Run:  BENCH_DB=qdrant python -m vector_database_tests.throughput \
          --qps 50 100 200 400 800 --duration 30 --warmup 5
"""
from logger import get_logger
from vector_database_tests.utils import registry

import os, json, time, argparse, threading, queue, statistics

LOGGER = get_logger(__name__)

SWEEP_DIR = "vector_database_tests/sweep_results"
RESULTS_DIR = "vector_database_tests/throughput_results"
QUERIES_DIR = "vector_database_tests/generated_queries"

DEFAULT_QPS_LADDER = [50, 100, 200, 400, 800]


def load_query_vectors(folder: str = QUERIES_DIR):
    """Return list of embedded query vectors (file order; we shuffle the issue order later)."""
    vecs = []
    for file_name in sorted(os.listdir(folder)):
        if not file_name.endswith(".jsonl"):
            continue
        with open(os.path.join(folder, file_name), "r", encoding = "utf-8") as f:
            for line in f:
                vecs.append(json.loads(line)["embedded_query"])
    return vecs


def load_search_param(db: str):
    """Use the sweep's chosen search_param (recall >= target). None -> client default."""
    path = os.path.join(SWEEP_DIR, f"{db}.json")
    if not os.path.exists(path):
        LOGGER.warning(f"No sweep result at {path}; using client default search_param.")
        return None
    with open(path, "r", encoding = "utf-8") as f:
        return json.load(f).get("chosen", {}).get("search_param")


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, int(q * len(sorted_vals)))
    return round(sorted_vals[idx], 3)


def run_level(client, collection, query_vecs, order, search_param,
              target_qps: float, duration_s: float, workers: int):
    """Drive `target_qps` requests/sec for `duration_s` seconds (open-loop) and return stats.

    The scheduler thread stamps each request with its IDEAL send time (start + i/qps) and drops
    it on a queue; worker threads execute as fast as they can. Latency is measured from the
    ideal send time, NOT from when a worker picked it up — this is what keeps the measurement
    coordinated-omission-safe under backlog."""
    interval = 1.0 / target_qps
    work = queue.Queue(maxsize = workers * 4)   # bounded -> backpressure, never unbounded memory
    latencies_ms, errors = [], [0]
    lat_lock = threading.Lock()
    stop = threading.Event()
    n_queries = len(order)

    def worker():
        while True:
            item = work.get()
            if item is None:
                work.task_done()
                return
            ideal_send, vec = item
            try:
                client.retrieve_ids(collection, vec, registry.TOP_K, search_param)
            except Exception:
                with lat_lock:
                    errors[0] += 1
                work.task_done()
                continue
            # Latency from the IDEAL send time: a request that waited in the backlog is charged
            # for that wait, exactly as a real client blocked on a slow server would be.
            elapsed = (time.perf_counter() - ideal_send) * 1000.0
            with lat_lock:
                latencies_ms.append(elapsed)
            work.task_done()

    pool = [threading.Thread(target = worker, daemon = True) for _ in range(workers)]
    for t in pool:
        t.start()

    start = time.perf_counter()
    sent = 0
    i = 0
    while not stop.is_set():
        now = time.perf_counter()
        if now - start >= duration_s:
            break
        ideal_send = start + sent * interval
        # Pace to the schedule: if we're ahead, wait until this request is due.
        sleep_for = ideal_send - now
        if sleep_for > 0:
            time.sleep(sleep_for)
        vec = query_vecs[order[i % n_queries]]
        work.put((ideal_send, vec))   # blocks if backlog is full -> the engine is the bottleneck
        sent += 1
        i += 1

    # Drain: let in-flight requests finish so their latency is counted.
    work.join()
    for _ in pool:
        work.put(None)
    for t in pool:
        t.join()

    wall = time.perf_counter() - start
    latencies_ms.sort()
    completed = len(latencies_ms)
    return {
        "target_qps": target_qps,
        "achieved_rps": round(completed / wall, 2) if wall > 0 else 0.0,
        "sent": sent,
        "completed": completed,
        "errors": errors[0],
        "median_ms": round(statistics.median(latencies_ms), 3) if latencies_ms else None,
        "p95_ms": _percentile(latencies_ms, 0.95),
        "p99_ms": _percentile(latencies_ms, 0.99),
    }


def run_throughput(db: str, qps_ladder, duration_s: float, warmup_s: float, workers: int) -> dict:
    collection = registry.normalize_collection(db, registry.DEFAULT_COLLECTION)
    client = registry.get_client(db)
    search_param = load_search_param(db)
    query_vecs = load_query_vectors()
    order = registry.shuffled_order(len(query_vecs))   # cache-fair issue order (fixed seed)

    # Warmup window (discarded) so the first timed rung doesn't pay cold-cache / lazy-load cost.
    if warmup_s > 0:
        LOGGER.info(f"[{db}] warmup {warmup_s}s ...")
        run_level(client, collection, query_vecs, order, search_param,
                  target_qps = qps_ladder[0], duration_s = warmup_s, workers = workers)

    levels = []
    for qps in qps_ladder:
        res = run_level(client, collection, query_vecs, order, search_param,
                        target_qps = qps, duration_s = duration_s, workers = workers)
        LOGGER.info(
            f"[{db}] target={qps}qps achieved={res['achieved_rps']}rps "
            f"median={res['median_ms']}ms p95={res['p95_ms']}ms p99={res['p99_ms']}ms "
            f"errors={res['errors']}"
        )
        levels.append(res)

    return {
        "db": db,
        "collection": collection,
        "search_param": search_param,
        "workers": workers,
        "duration_s": duration_s,
        "levels": levels,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description = "Open-loop fixed-QPS throughput benchmark for one DB")
    p.add_argument("--db", default = None)
    p.add_argument("--qps", type = float, nargs = "+", default = DEFAULT_QPS_LADDER,
                   help = "QPS ladder to sweep (target arrival rates).")
    p.add_argument("--duration", type = float, default = 30.0, help = "Seconds per QPS rung.")
    p.add_argument("--warmup", type = float, default = 5.0, help = "Discarded warmup seconds.")
    p.add_argument("--workers", type = int, default = 64,
                   help = "Worker threads executing client calls (the concurrency ceiling).")
    args = p.parse_args()

    db = registry.resolve_db(args.db)
    result = run_throughput(db, args.qps, args.duration, args.warmup, args.workers)

    os.makedirs(RESULTS_DIR, exist_ok = True)
    out_path = os.path.join(RESULTS_DIR, f"{db}.json")
    with open(out_path, "w", encoding = "utf-8") as f:
        json.dump(result, f, indent = 2)
    LOGGER.info(f"Wrote throughput result -> {out_path}")
    print(json.dumps(result, indent = 2))
