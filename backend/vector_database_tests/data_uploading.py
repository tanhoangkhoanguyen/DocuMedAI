from logger import get_logger
from vector_database_tests.utils.chromadb_client import ChromadbClient
from vector_database_tests.utils.milvus_client import MilvusClient
from vector_database_tests.utils.pinecone_client import PineconeClient
from vector_database_tests.utils.qdrant_client import QdrantClient
from vector_database_tests.utils.vespa_client import VespaClient
from vector_database_tests.utils.weaviate_client import WeaviateClient

import os, json, time, warnings
warnings.filterwarnings("ignore")

LOGGER = get_logger(__name__)

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
        ):
        try:
            self.client.create_collection(self.collection_name)

            batch_size = 1000
            ids, queries, embedded_queries = [], [], []
            total_indexing_time = 0

            def indexing():
                nonlocal total_indexing_time, ids, queries, embedded_queries
                start_time = time.perf_counter()
                self.client.push_documents(
                    self.collection_name, ids, queries, embedded_queries
                )
                end_time = time.perf_counter()
                total_indexing_time += end_time - start_time
                ids, queries, embedded_queries = [], [], []

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
                            indexing()
            if ids:
                indexing()
            start_time = time.perf_counter()
            # self.client._milvus_client.flush()
            # self.client.create_index(self.collection_name)
            end_time = time.perf_counter()
            total_indexing_time += end_time - start_time
            LOGGER.info(f"Uploaded dataset for vector db test\n\t- Total indexing time: {total_indexing_time}s")
        except Exception as e:
            LOGGER.error(f"Failed to upload dataset for vector db test\n\t{str(e)}")
            raise

if __name__ == "__main__":
    client = WeaviateClient(
            embedding_model = "sentence-transformers/all-MiniLM-L6-v2",
            embedding_dimension = 384
        )
    collection_name = "LatencyTest" # "latencytest"
    data_uploading_test = DataUploading(client, collection_name)
    data_uploading_test.upload_dataset()
    print (data_uploading_test.client.list_collections())