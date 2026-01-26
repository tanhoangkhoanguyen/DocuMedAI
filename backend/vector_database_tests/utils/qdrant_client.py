import os, sys, json, uuid, pytz, time, statistics, warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()
from langchain_community.embeddings import HuggingFaceEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.http.models import VectorParams, Distance, PointStruct
from datetime import datetime
from queue import Queue

QDRANT_URL = "http://la-qdrant:6333"

class QdrantSetup:
    def __init__(
            self, 
            embedding_model: str,
            embedding_dimension: int,
            collection_name: str = "latency_test"
        ):
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__embedding_dimension = embedding_dimension
        self.__collection_name = collection_name
        self.__client = QdrantClient(url = QDRANT_URL)
        self.__uuid_namespace = uuid.UUID(os.getenv("UUID_NAMESPACE"))

    def __create_collection(
            self, 
            collection_name: str
        ):
        try:
            if self.__client.collection_exists(collection_name):
                self.__client.delete_collection(collection_name)
            self.__client.create_collection(
                collection_name = self.__collection_name,
                vectors_config = VectorParams(
                    size = self.__embedding_dimension, 
                    distance = Distance.COSINE
                )
            )
            print(f"""
                [INFO] [backend.data_setup.qdrant_setup] Created collection '{collection_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.qdrant_setup] Failed to create collection '{collection_name}'
                \t{str(e)}
            """)
            sys.exit()

    def __embed_query(self, query: str):
        embedded_text = self.__embedding_model.embed_query(query)
        return embedded_text

    def __push_to_collection(
            self,
            collection_name: str,
            embedded_queries: list
        ):
        try:
            points = []
            for embedded_query in embedded_queries:
                id = str(datetime.now(pytz.utc))
                hashed_id = uuid.uuid5(self.__uuid_namespace, id)
                points.append(
                    PointStruct(
                        id = hashed_id,
                        vector = embedded_query,
                        payload = {"query": ""}
                ))

            self.__client.upsert(
                collection_name = collection_name,
                points = points
            )
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.qdrant_setup] Failed to push to collection '{collection_name}'
                \t{str(e)}
            """)

    def __retrieve_query(
            self,
            collection_name: str,
            embedded_query: list,
            top_k: int = 50
        ):
        response = self.__client.query_points(
            collection_name = collection_name,
            query = embedded_query,
            limit = top_k,
            with_payload = False,
            with_vectors = False
        )
        return response

    def execute(
            self,
            dataset_name: str,
            input_dataset_path: str = "vector_database_tests/encoded_dataset",
            input_questions_path: str = "vector_database_tests/generated_questions.json"
        ):
        input_dataset_path = os.path.join(input_dataset_path, dataset_name.replace("/", "-") + ".jsonl")
        self.__create_collection(self.__collection_name)

        batch_size = 100
        q = Queue()
        with open (input_dataset_path, 'r', encoding = "utf-8") as f:
            batch = []
            for line in f:
                record = json.loads(line)
                batch.append(record["embedding"])

                if len(batch) == batch_size:
                    q.put(batch)
                    batch = []
            if batch:
                q.put(batch)
        
        while not q.empty():
            batch = q.get()
            self.__push_to_collection(self.__collection_name, batch)

        record_time = []
        warm_up_counter = 0
        with open(input_questions_path, 'r', encoding = "utf-8") as f:
            questions = json.load(f)[dataset_name]
            for question in questions:
                embedded_question = self.__embed_query(question)

                start_time = time.perf_counter()
                self.__retrieve_query(self.__collection_name, embedded_question)
                end_time = time.perf_counter()

                warm_up_counter += 1
                if warm_up_counter > 50:
                    record_time.append((end_time - start_time) * 1000)
        
        record_time.sort()

        n = len(record_time)
        print ("\n\n\nQdrant vector DB latency report:")
        print(f"Avg: {statistics.mean(record_time):.2f} ms")
        print(f"p50: {record_time[int(0.50 * n)]:.2f} ms")
        print(f"p95: {record_time[int(0.95 * n)]:.2f} ms")
        print(f"p99: {record_time[int(0.99 * n)]:.2f} ms")

        self.__client.close()

if __name__ == "__main__":
    dataset_name = "gamino/wiki_medical_terms"
    embedding_model = "all-MiniLM-L6-v2"
    embedding_dimension = 384

    qdrant_setup = QdrantSetup(
        embedding_model = embedding_model,
        embedding_dimension = embedding_dimension
    )
    qdrant_setup.execute(dataset_name = dataset_name)