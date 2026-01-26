import os, sys, json, uuid, pytz, time, statistics, warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()
from langchain_community.embeddings import HuggingFaceEmbeddings
from pymilvus import connections, FieldSchema, CollectionSchema, DataType, Collection, utility
from datetime import datetime
from queue import Queue

MILVUS_HOST = "la-milvus"#"milvus-standalone"
MILVUS_PORT = "19530"

class MilvusSetup:
    def __init__(
            self,
            embedding_model: str,
            embedding_dimension: int,
            collection_name: str = "latency_test"
        ):
        self.__embedding_model = HuggingFaceEmbeddings(model_name=embedding_model)
        self.__embedding_dimension = embedding_dimension
        self.__collection_name = collection_name
        self.__collection = None
        self.__uuid_namespace = uuid.UUID(os.getenv("UUID_NAMESPACE"))

        connections.connect(
            alias = "default",
            host = MILVUS_HOST,
            port = MILVUS_PORT
        )

    def __create_collection(
            self, 
            collection_name: str
        ):
        try:
            if utility.has_collection(collection_name):
                utility.drop_collection(collection_name)

            fields = [
                FieldSchema(
                    name = "id",
                    dtype = DataType.VARCHAR,
                    max_length = 64,
                    is_primary = True,
                    auto_id = False
                ),
                FieldSchema(
                    name = "embedding",
                    dtype = DataType.FLOAT_VECTOR,
                    dim = self.__embedding_dimension
                ),
                FieldSchema(
                    name = "query",
                    dtype = DataType.VARCHAR,
                    max_length = 1024
                )
            ]

            schema = CollectionSchema(
                fields = fields,
                description = "Latency test collection"
            )

            self.__collection = Collection(
                name = collection_name,
                schema = schema
            )

            self.__collection.create_index(
                field_name = "embedding",
                index_params = {
                    "index_type": "HNSW",
                    "metric_type": "COSINE",
                    "params": {
                        "M": 16,
                        "efConstruction": 200
                    }
                }
            )

            self.__collection.load()

            print(f"""
                [INFO] [backend.data_setup.milvus_setup] Created collection '{collection_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.milvus_setup] Failed to create collection '{collection_name}'
                \t{str(e)}
            """)
            sys.exit()

    def __embed_query(self, query: str):
        embedded_text = self.__embedding_model.embed_query(query)
        return embedded_text

    def __bind_collection(
            self,
            collection_name: str
        ):
        if not utility.has_collection(collection_name):
            raise ValueError(f"Collection {collection_name} does not exist")

        self.__collection = Collection(collection_name)
        self.__collection.load()

    def __push_to_collection(
            self,
            collection_name: str,
            embedded_queries: list
        ):
        try:
            hashed_ids = []
            metas = []
            for _ in embedded_queries:
                id = str(datetime.now(pytz.utc))
                hashed_id = str(uuid.uuid5(self.__uuid_namespace, id))
                hashed_ids.append(hashed_id)
                metas.append("")

            data = [
                hashed_ids,
                embedded_queries,
                metas
            ]

            self.__bind_collection(collection_name)
            self.__collection.insert(data)
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.milvus_setup] Failed to push to collection '{collection_name}'
                \t{str(e)}
            """)

    def __retrieve_query(
            self,
            collection_name: str,
            embedded_query: list,
            top_k: int = 50
        ):
        search_params = {
            "metric_type": "COSINE",
            "params": {
                "ef": 64
            }
        }

        self.__bind_collection(collection_name)
        response = self.__collection.search(
            data = [embedded_query],
            anns_field = "embedding",
            param = search_params,
            limit = top_k,
            output_fields = []
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
        with open(input_dataset_path, "r", encoding="utf-8") as f:
            batch = []
            for line in f:
                record = json.loads(line)
                batch.append(record["embedding"])

                if len(batch) == batch_size:
                    q.put(batch)
                    batch = []
                    break
            if batch:
                q.put(batch)
        
        while not q.empty():
            batch = q.get()
            self.__push_to_collection(self.__collection_name, batch)
        self.__collection.flush()

        record_time = []
        warm_up_counter = 0
        with open(input_questions_path, "r", encoding="utf-8") as f:
            questions = json.load(f)[dataset_name]
            for question in questions:
                embedded_question = self.__embed_query(question)

                start_time = time.perf_counter()
                self.__retrieve_query(self.__collection_name, embedded_question)
                end_time = time.perf_counter()

                warm_up_counter += 1
                if warm_up_counter > 50:
                    record_time.append((end_time - start_time) * 1000)
                break

        record_time.sort()
        
        # n = len(record_time)
        # print("\n\n\nMilvus vector DB latency report:")
        # print(f"Avg: {statistics.mean(record_time):.2f} ms")
        # print(f"p50: {record_time[int(0.50 * n)]:.2f} ms")
        # print(f"p95: {record_time[int(0.95 * n)]:.2f} ms")
        # print(f"p99: {record_time[int(0.99 * n)]:.2f} ms")

        connections.disconnect("default")

if __name__ == "__main__":
    dataset_name = "gamino/wiki_medical_terms"
    embedding_model = "all-MiniLM-L6-v2"
    embedding_dimension = 384

    milvus_setup = MilvusSetup(
        embedding_model = embedding_model,
        embedding_dimension = embedding_dimension
    )
    milvus_setup.execute(dataset_name)