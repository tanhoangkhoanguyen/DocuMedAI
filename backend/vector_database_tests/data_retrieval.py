# locust -f vector_database_tests/data_retrieval.py
from vector_database_tests.utils.chromadb_client import ChromadbClient
from vector_database_tests.utils.milvus_client import MilvusClient 
from vector_database_tests.utils.pinecone_client import PineconeClient 
from vector_database_tests.utils.qdrant_client import QdrantClient
from vector_database_tests.utils.vespa_client import VespaClient 
from vector_database_tests.utils.weaviate_client import WeaviateClient

import os, time, json, random, logging
from locust import User, task, between, events

logging.getLogger("httpx").setLevel(logging.WARNING)

def load_queries(folder_path: str = "vector_database_tests/generated_queries") -> list:
    queries = []
    for file_name in os.listdir(folder_path):
        if not file_name.endswith(".jsonl"):
            continue

        file_path = os.path.join(folder_path, file_name)
        with open(file_path, "r", encoding = "utf-8") as f:
            queries.extend([json.loads(obj) for obj in f])
    return queries

SHARED_CLIENT = None
def get_shared_client():
    global SHARED_CLIENT
    if SHARED_CLIENT is None:
        SHARED_CLIENT = WeaviateClient(
            embedding_model = "sentence-transformers/all-MiniLM-L6-v2",
            embedding_dimension = 384,
        )
    return SHARED_CLIENT


QUERIES = load_queries()
LEN_QUERIES = len(QUERIES)

class VectorDBUser(User):
    host = "http://localhost" # dummy value
    wait_time = between(1, 2)

    def on_start(self):
        self.collection_name = "LatencyTest" # "latencytest"
        self.querying_docs = random.sample(QUERIES, LEN_QUERIES)
        self.len_docs = LEN_QUERIES
        self.client = get_shared_client()
        self.idx = 0

    @task
    def single_vector_search(self):
        if self.environment.runner and self.environment.runner.state != "running":
            return

        embedded_query = self.querying_docs[self.idx % self.len_docs]["embedded_query"]
        exception = None
        start_time = time.perf_counter()

        try:
            response = self.client.retrieve_query(self.collection_name, embedded_query)
        except Exception as e:
            exception = e
        finally:
            end_time = time.perf_counter()
            events.request.fire(
                request_type = "VECTOR",
                name = " ",
                response_time = (end_time - start_time) * 1000,
                response_length = 0,
                exception = exception,
            )
        self.idx += 1