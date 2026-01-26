# import os, sys, json, uuid, pytz, time, statistics, warnings
# warnings.filterwarnings("ignore")
# from dotenv import load_dotenv
# load_dotenv()
# from langchain_community.embeddings import HuggingFaceEmbeddings
# import weaviate
# from datetime import datetime

# WEAVIATE_URL = "http://la-weaviate:8080"

# class WeaviateSetup:
#     def __init__(
#             self, 
#             embedding_model: str,
#             embedding_dimension: int,
#             class_name: str = "latency_test"     # 512 tokens * 4
#         ):
#         self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
#         self.__embedding_dimension = embedding_dimension
#         self.__class_name = class_name
#         self.__client = weaviate.Client(url = WEAVIATE_URL)
#         self.__uuid_namespace = uuid.UUID(os.getenv("UUID_NAMESPACE"))

#     def __create_class(
#             self,
#             class_name: str
#         ):
#         try:
#             if self.__client.schema.contains({"class": class_name}):
#                 self.__client.schema.delete_class(class_name)
#             self.__client.schema.create_class({
#                 "class": class_name,
#                 "vectorizer": "none",
#                 "properties": [
#                     {
#                         "name": "query",
#                         "dataType": ["string"]
#                     }
#                 ]
#             })
#             print(f"""
#                 [INFO] [backend.data_setup.weaviate_setup] Created class '{class_name}'
#             """)
#         except Exception as e:
#             print(f"""
#                 [ERROR] [backend.data_setup.weaviate_setup] Failed to create class '{class_name}'
#                 \t{str(e)}
#             """)
#             sys.exit()

#     def __embed_query(self, query: str):
#         embedded_text = self.__embedding_model.embed_query(query)
#         return embedded_text

#     def __push_to_class(
#             self,
#             class_name: str,
#             embedded_query: list
#         ):
#         try:
#             id = str(datetime.now(pytz.utc))
#             hashed_id = str(uuid.uuid5(self.__uuid_namespace, id))

#             self.__client.data_object.create(
#                 data_object = {"query": ""},
#                 class_name = class_name,
#                 vector = embedded_query,
#                 uuid = hashed_id
#             )
#         except Exception as e:
#             print(f"""
#                 [ERROR] [backend.data_setup.weaviate_setup] Failed to push to class name '{class_name}'
#                 \t{str(e)}
#             """)

#     def __retrieve_query(
#             self, 
#             class_name: str,
#             embedded_query: list, 
#             top_k: int = 50
#         ):
#         response = self.__client.query.get(class_name, ["*"]) \
#             .with_near_vector({"vector": embedded_query}) \
#             .with_limit(top_k) \
#             .do()
#         return response.get("data", {}).get("Get", {}).get(class_name, [])

#     def execute(
#             self,
#             dataset_name: str,
#             input_dataset_path: str = "vector_database_tests/encoded_dataset",
#             input_questions_path: str = "vector_database_tests/generated_questions.jsonl"
#         ):
#         input_dataset_path = os.path.join(input_dataset_path, dataset_name.replace("/", "-") + ".jsonl")
#         self.__create_class(self.__class_name)
        
#         with open(input_dataset_path, 'r', encoding = "utf-8") as f:
#             for line in f:
#                 record = json.loads(line)
#                 self.__push_to_class(self.__class_name, record["embedding"])

#         record_time = []
#         with open(input_questions_path, 'r', encoding = "utf-8") as f:
#             questions = json.load(f)[dataset_name]
#             for question in questions:
#                 embedded_question = self.__embed_query(question)
#                 start_time = time.perf_counter()
#                 self.__retrieve_query(self.__class_name, embedded_question)
#                 end_time = time.perf_counter()
#                 record_time.append((end_time - start_time) * 1000)
        
#         record_time.sort()

#         n = len(record_time)
#         print ("\n\n\nWeaviate vector DB latency report:")
#         print(f"Avg: {statistics.mean(record_time):.2f} ms")
#         print(f"p50: {record_time[int(0.50 * n)]:.2f} ms")
#         print(f"p95: {record_time[int(0.95 * n)]:.2f} ms")
#         print(f"p99: {record_time[int(0.99 * n)]:.2f} ms")

# if __name__ == "__main__":
#     dataset_name = "gamino/wiki_medical_terms"
#     embedding_model = "all-MiniLM-L6-v2"
#     embedding_dimension = 384

#     weaviate_setup = WeaviateSetup(
#         embedding_model = embedding_model,
#         embedding_dimension = embedding_dimension
#     )
#     weaviate_setup.execute(dataset_name)









import weaviate
import random

client = weaviate.Client("http://localhost:8004")

CLASS_NAME = "VectorClass"

# Delete old class
try:
    client.schema.delete_class(CLASS_NAME)
except Exception:
    pass

# Create class with NO automatic vectorization
client.schema.create_class({
    "class": CLASS_NAME,
    "vectorizer": "none",  # <- no auto embedding
    "properties": [
        {"name": "text", "dataType": ["string"]}
    ]
})

# Insert precomputed vectors (384D for example)
vectors = [
    ([random.random() for _ in range(384)], "Hello world"),
    ([random.random() for _ in range(384)], "Vector DB test"),
    ([random.random() for _ in range(384)], "Weaviate manual vectors")
]

for vec, txt in vectors:
    client.data_object.create(
        data_object={"text": txt},
        class_name=CLASS_NAME,
        vector=vec  # <- push your precomputed embedding
    )

print("[INFO] Vectors upserted")

# Query with a precomputed vector
query_vector = [random.random() for _ in range(384)]

res = client.query.get(CLASS_NAME, ["text"]) \
    .with_near_vector({"vector": query_vector}) \
    .with_limit(1) \
    .do()

print("Query result:", res.get("data", {}).get("Get", {}).get(CLASS_NAME, []))
