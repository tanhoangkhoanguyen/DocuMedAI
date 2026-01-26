# import os, sys, json, uuid, pytz, time, statistics, warnings
# warnings.filterwarnings("ignore")

# from dotenv import load_dotenv
# load_dotenv()

# from langchain_community.embeddings import HuggingFaceEmbeddings
# from pinecone import Pinecone, PodSpec, ServerlessSpec
# from datetime import datetime
# from queue import Queue


# PINECONE_API_KEY = "pclocal"
# PINECONE_URL = "http://la-pinecone:5081"

# class PineconeSetup:
#     def __init__(
#             self,
#             embedding_model: str,
#             embedding_dimension: int,
#             index_name: str = "latency-test"
#         ):
#         self.__embedding_model = HuggingFaceEmbeddings(
#             model_name=embedding_model
#         )
#         self.__embedding_dimension = embedding_dimension
#         self.__index_name = index_name
#         self.__pinecone = Pinecone(
#                 api_key = PINECONE_API_KEY,
#                 host = PINECONE_URL
#             )
#         self.__uuid_namespace = uuid.UUID(os.getenv("UUID_NAMESPACE"))

#     def __create_index(self):
#         try:
#             if self.__index_name in self.__pinecone.list_indexes().names():
#                 self.__pinecone.delete_index(self.__index_name)

#             self.__pinecone.create_index(
#                 name = self.__index_name,
#                 dimension = self.__embedding_dimension,
#                 metric = "cosine",
#                 spec = ServerlessSpec(
#                     cloud = "aws",
#                     region = "us-east-1"
#                 )
#             )

#             self.__index = self.__pinecone.Index(self.__index_name)

#             print(f"""
#                 [INFO] [backend.data_setup.pinecone_setup] Created index '{self.__index_name}'
#             """)
#         except Exception as e:
#             print(f"""
#                 [ERROR] [backend.data_setup.pinecone_setup] Failed to create index '{self.__index_name}'
#                 \t{str(e)}
#             """)
#             sys.exit()

#     def __embed_query(self, query: str):
#         embedded_text = self.__embedding_model.embed_query(query)
#         return embedded_text

#     def __push_to_collection(
#             self,
#             embedded_queries: list
#         ):
#         try:
#             vectors = []

#             for embedded_query in embedded_queries:
#                 id = str(datetime.now(pytz.utc))
#                 hashed_id = str(uuid.uuid5(self.__uuid_namespace, id))

#                 vectors.append((
#                     hashed_id,
#                     embedded_query,
#                     {"query": ""}
#                 ))

#             self.__index.upsert(vectors = vectors)
#         except Exception as e:
#             print(f"""
#                 [ERROR] [backend.data_setup.pinecone_setup] Failed to upsert vectors
#                 \t{str(e)}
#             """)

#     def __retrieve_query(
#             self,
#             embedded_query: list,
#             top_k: int = 50
#         ):
#         response = self.__index.query(
#             vector = embedded_query,
#             top_k = top_k,
#             include_metadata = False,
#             include_values = False
#         )
#         return response

#     def execute(
#         self,
#         dataset_name: str,
#         input_dataset_path: str = "vector_database_tests/encoded_dataset",
#         input_questions_path: str = "vector_database_tests/generated_questions.json"
#     ):
#         input_dataset_path = os.path.join(
#             input_dataset_path,
#             dataset_name.replace("/", "-") + ".jsonl"
#         )

#         self.__create_index()

#         batch_size = 100
#         q = Queue()

#         with open(input_dataset_path, "r", encoding = "utf-8") as f:
#             batch = []
#             for line in f:
#                 record = json.loads(line)
#                 batch.append(record["embedding"])

#                 if len(batch) == batch_size:
#                     q.put(batch)
#                     batch = []
#                     break
#             if batch:
#                 q.put(batch)

#         while not q.empty():
#             self.__push_to_collection(q.get())

#         return

#         record_time = []
#         warm_up_counter = 0

#         with open(input_questions_path, "r", encoding="utf-8") as f:
#             questions = json.load(f)[dataset_name]
#             for question in questions:
#                 embedded_question = self.__embed_query(question)

#                 start = time.perf_counter()
#                 self.__retrieve_query(embedded_question)
#                 end = time.perf_counter()

#                 warm_up_counter += 1
#                 if warm_up_counter > 50:
#                     record_time.append((end - start) * 1000)

#         record_time.sort()
#         n = len(record_time)

#         print("\n\n\nPinecone vector DB latency report:")
#         print(f"Avg: {statistics.mean(record_time):.2f} ms")
#         print(f"p50: {record_time[int(0.50 * n)]:.2f} ms")
#         print(f"p95: {record_time[int(0.95 * n)]:.2f} ms")
#         print(f"p99: {record_time[int(0.99 * n)]:.2f} ms")


# if __name__ == "__main__":
#     dataset_name = "gamino/wiki_medical_terms"
#     embedding_model = "all-MiniLM-L6-v2"
#     embedding_dimension = 384

#     pinecone_setup = PineconeSetup(
#         embedding_model = embedding_model,
#         embedding_dimension = embedding_dimension
#     )

#     pinecone_setup.execute(dataset_name = dataset_name)
from pinecone import Pinecone, ServerlessSpec

# Control plane (index management)
pc = Pinecone(
    api_key="pclocal",
    host="http://localhost:8002"
)

INDEX_NAME = "test-index"
DIM = 3

# Create index with ServerlessSpec
if INDEX_NAME not in pc.list_indexes().names():
    pc.create_index(
        name=INDEX_NAME,
        dimension=DIM,
        metric="cosine",
        spec=ServerlessSpec(
            cloud="aws",
            region="us-east-1"
        )
    )

# Connect to index - remove api_key parameter
index = pc.Index(
    host="http://localhost:9003"
)

# Upsert vectors
index.upsert([
    ("vec1", [1.0, 0.0, 0.0], {"label": "x"}),
    ("vec2", [0.0, 1.0, 0.0], {"label": "y"})
])

# Query
res = index.query(
    vector=[1.0, 0.0, 0.0],
    top_k=2,
    include_metadata=True
)

print("Query result:", res)