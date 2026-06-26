"""
Indexing benchmark: insert the ~1M-vector corpus into the selected DB and record total
indexing time (insert + index build), excluding dataset loading from disk.

Run per DB:  BENCH_DB=qdrant python -m vector_database_tests.data_uploading
         or  python -m vector_database_tests.data_uploading --db qdrant
"""
from logger import get_logger
from vector_database_tests.utils import registry

import os, json, time, argparse, warnings
warnings.filterwarnings("ignore")

LOGGER = get_logger(
    name = "data_uploading",
    level = "INFO",
)

RESULTS_DIR = "vector_database_tests/upload_results"


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
            folder_path: str = "vector_database_tests/dataset"
        ) -> dict:
        try:
            self.client.create_collection(self.collection_name)

            batch_size = 1000
            ids, queries, embedded_queries = [], [], []
            total_indexing_time = 0.0
            n_vectors = 0

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
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    db = registry.resolve_db(args.db)
    collection = registry.normalize_collection(db, args.collection)

    client = registry.get_client(db)
    result = DataUploading(client, collection).upload_dataset()
    result["db"] = db

    os.makedirs(RESULTS_DIR, exist_ok = True)
    out_path = os.path.join(RESULTS_DIR, f"{db}.json")
    with open(out_path, "w", encoding = "utf-8") as f:
        json.dump(result, f, indent = 2)
    LOGGER.info(f"Wrote indexing result -> {out_path}\n\t{result}")
