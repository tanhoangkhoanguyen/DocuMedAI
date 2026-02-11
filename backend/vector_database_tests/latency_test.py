from vector_database_tests.utils.chromadb_client import ChromadbClient
from vector_database_tests.utils.milvus_client import MilvusClient 
from vector_database_tests.utils.pinecone_client import PineconeClient 
from vector_database_tests.utils.qdrant_client import QdrantClient 
from vector_database_tests.utils.vespa_client import VespaClient 
from vector_database_tests.utils.weaviate_client import WeaviateClient

import os, json, time, warnings
warnings.filterwarnings("ignore")
from concurrent.futures import ThreadPoolExecutor, as_completed

class LatencyTest:
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
        ):
        try:
            self.client.create_collection(self.collection_name)

            batch_size = 100
            ids, queries, embedded_queries = [], [], []
            total_indexing_time = 0
            for filename in os.listdir(folder_path):
                if not filename.endswith(".jsonl"):
                    continue

                file_path = os.path.join(folder_path, filename)
                print (file_path)
                with open(file_path, 'r', encoding = "utf-8") as f:
                    for obj in f:
                        object = json.loads(obj)
                        ids.append(object["id"])
                        queries.append(object["split_text"])
                        embedded_queries.append(object["embedded_test"])

                        if len(ids) == batch_size:
                            start_time = time.perf_counter()
                            self.client.push_to_collection(
                                self.collection_name, ids, queries, embedded_queries
                            )
                            end_time = time.perf_counter()
                            total_indexing_time += end_time - start_time
                            ids, queries, embedded_queries = [], [], []
            if ids:
                self.client.push_to_collection(
                    self.collection_name, ids, queries, embedded_queries
                )
            print(f"""
                [INFO] [backend.vector_database_tests.data_processing] Uploaded dataset for vector db test
                \t- Total indexing time: {total_indexing_time}s
            """)
        except Exception as e: 
            print(f""" 
                [ERROR] [backend.vector_database_tests.latency_test] Failed to upload dataset for vector db test 
                \t{str(e)}
            """) 
            raise

    def __worker_func(
            self,
            embedded_query,
        ):
        start_time = time.perf_counter()
        response = self.client.retrieve_query(self.collection_name, embedded_query)
        end_time = time.perf_counter()
        return end_time - start_time

    def retrieve_data(
            self,
            folder_path: str = "vector_database_tests/generated_queries",
            max_workers: int = 100,
            warm_up_size: int = 50
        ):
        queries = []
        for file_name in os.listdir(folder_path):
            if not file_name.endswith(".jsonl"):
                continue

            file_path = os.path.join(folder_path, file_name)
            with open(file_path, 'r', encoding = "utf-8") as f:
                queries.extend([json.loads(obj) for obj in f])

        with ThreadPoolExecutor(max_workers = max_workers) as executor:
            futures = [
                executor.submit(self.__worker_func, obj["embedded_query"])
                for obj in queries
            ]

            try:
                latencies = []
                warm_up_counter = 0
                for future in as_completed(futures):
                    warm_up_counter += 1
                    if warm_up_counter > warm_up_size:
                        latencies.append(future.result())
            except Exception as e:
                executor.shutdown(wait = False, cancel_futures = True)
                print(f"""
                    [ERROR] [backend.vector_database_tests.latency_test] Failed to retrieve data for vector db test
                    \t{str(e)}
                """)
                raise

        latencies.sort()
        p50 = p95 = 1
        if latencies:
            p50 = latencies[int(0.50 * len(latencies)) - 1]
            p95 = latencies[int(0.95 * len(latencies)) - 1]
        print(f"""
            [INFO] [backend.vector_database_tests.data_processing] Latency test results:
            \t- P50 latency: {p50}s
            \t- P95 latency: {p95}s
        """)

    def latency_test(self):
        self.upload_dataset()
        self.retrieve_data()

if __name__ == "__main__":
    client = VespaClient(
            embedding_model = "sentence-transformers/all-MiniLM-L6-v2",
            embedding_dimension = 384
        )
    collection_name = "LatencyTest"
    latency_test = LatencyTest(client, collection_name)
    # latency_test.latency_test()
    print (latency_test.client.list_collections())