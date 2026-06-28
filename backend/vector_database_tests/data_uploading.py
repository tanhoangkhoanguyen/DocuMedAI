"""
Indexing benchmark: insert the 1M-vector corpus into the selected DB and record total
indexing time (insert + index build), excluding dataset loading from disk.

Run per DB:  BENCH_DB=qdrant python -m vector_database_tests.data_uploading
         or  python -m vector_database_tests.data_uploading --db qdrant
"""
from logger import get_logger
from vector_database_tests.utils import registry

import os, sys, json, time, argparse, warnings
warnings.filterwarnings("ignore")

LOGGER = get_logger(
    name = "vectordb_lab_data_uploading",
    level = "INFO",
)

RESULTS_DIR = "vector_database_tests/upload_results"
MAX_UPLOAD_SECONDS = 3600                                         # Abort this DB's upload and move on to the next


class UploadTimeout(Exception):
    """Raised when a DB's upload exceeds the wall-clock budget so the run skips to the next DB."""


class DataUploading:
    def __init__(
            self,
            client,
            collection_name: str = "latency_test"
        ):
        self.client = client
        self.collection_name = collection_name

    def upload_dataset(
            self,
            folder_path: str = "vector_database_tests/dataset",
            max_seconds: float = MAX_UPLOAD_SECONDS,
        ) -> dict:
        try:
            self.client.create_collection(self.collection_name)

            batch_size = 1000
            ids, queries, embedded_queries = [], [], []
            total_indexing_time = 0.0
            n_vectors = 0
            wall_start = time.perf_counter()                      # includes disk reads — total budget for this DB

            def indexing():
                nonlocal total_indexing_time, ids, queries, embedded_queries, n_vectors
                start_time = time.perf_counter()
                self.client.push_documents(
                    self.collection_name, ids, queries, embedded_queries
                )
                end_time = time.perf_counter()
                total_indexing_time += end_time - start_time
                n_vectors += len(ids)
                ids, queries, embedded_queries = [], [], []
                elapsed = time.perf_counter() - wall_start
                if elapsed > max_seconds:
                    raise UploadTimeout(
                        f"Upload exceeded {max_seconds:.0f}s "
                        f"(elapsed {elapsed:.0f}s, {n_vectors} vectors indexed so far)"
                    )

            for filename in sorted(os.listdir(folder_path)):
                if not filename.endswith(".jsonl"):
                    continue

                file_path = os.path.join(folder_path, filename)
                LOGGER.info(f"Reading {file_path}")
                with open(file_path, 'r', encoding = "utf-8") as f:
                    for obj in f:
                        record = json.loads(obj)
                        ids.append(record["id"])
                        queries.append(record["split_text"])
                        embedded_queries.append(record["embedded_test"])

                        if len(ids) == batch_size:
                            indexing()
            if ids:
                indexing()

            # Only Milvus needs an explicit build; the others index on insert.
            if hasattr(self.client, "create_index"):
                start_time = time.perf_counter()
                self.client.create_index(self.collection_name)
                total_indexing_time += time.perf_counter() - start_time

            LOGGER.info(
                f"Uploaded {n_vectors} vectors\n\t- Total indexing time: {total_indexing_time:.2f}s"
            )
            return {
                "collection": self.collection_name,
                "total_indexing_time_s": round(total_indexing_time, 3),
                "n_vectors": n_vectors,
            }
        except Exception as e:
            LOGGER.error(f"Failed to upload dataset for vector db test\n\t{str(e)}")
            raise


def _parse_args():
    p = argparse.ArgumentParser(description = "Vector DB indexing benchmark")
    p.add_argument("--db", default = None, help = f"One of {registry.SUPPORTED} (or set BENCH_DB)")
    p.add_argument("--collection", default = registry.DEFAULT_COLLECTION)
    p.add_argument("--max-seconds", type = float, default = MAX_UPLOAD_SECONDS,
                   help = "Abort this DB's upload if it exceeds this wall-clock budget (default 3600 = 1h)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    db = registry.resolve_db(args.db)
    collection = registry.normalize_collection(db, args.collection)

    client = registry.get_client(db)

    os.makedirs(RESULTS_DIR, exist_ok = True)
    out_path = os.path.join(RESULTS_DIR, f"{db}.json")

    try:
        result = DataUploading(client, collection).upload_dataset(max_seconds = args.max_seconds)
        result["db"] = db
        result["timed_out"] = False
    except UploadTimeout as e:
        # Mark this DB as skipped and exit non-zero so the caller knows; the loop moves on.
        LOGGER.error(f"[{db}] upload timed out — skipping remaining tests for this DB\n\t{e}")
        result = {"db": db, "timed_out": True, "reason": str(e), "max_seconds": args.max_seconds}
        with open(out_path, "w", encoding = "utf-8") as f:
            json.dump(result, f, indent = 2)
        sys.exit(1)

    with open(out_path, "w", encoding = "utf-8") as f:
        json.dump(result, f, indent = 2)
    LOGGER.info(f"Wrote indexing result -> {out_path}\n\t{result}")
