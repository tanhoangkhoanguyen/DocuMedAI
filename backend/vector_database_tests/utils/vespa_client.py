# import os, sys, json, uuid, pytz, time, statistics, warnings
# warnings.filterwarnings("ignore")
# from dotenv import load_dotenv
# load_dotenv()
# from langchain_community.embeddings import HuggingFaceEmbeddings
# from datetime import datetime
# from vespa.application import Vespa
# from vespa.query import Query, RankProfile

# VESPA_URL = "http://localhost:8080"

# class VespaSetup:
#     def __init__(
#             self, 
#             embedding_model: str,
#             embedding_dimension: int,
#             app_name: str = "latency_test"
#         ):
#         self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
#         self.__embedding_dimension = embedding_dimension
#         self.__app_name = app_name
#         self.__vespa = Vespa(url = VESPA_URL)
#         self.__uuid_namespace = uuid.UUID(os.getenv("UUID_NAMESPACE"))

#     def __push_to_vespa(
#             self, 
#             app_name: str, 
#             embedded_query: list
#         ):
#         try:
#             id = str(datetime.now(pytz.utc))
#             hashed_id = str(uuid.uuid5(self.__uuid_namespace, id))

#             self.__vespa.feed_data_point(
#                 schema = app_name,
#                 doc_id = hashed_id,
#                 fields = {"query": ""},
#                 vector_fields = {"query_vector": embedded_query}
#             )
#         except Exception as e:
#             print(f"""
#                 [ERROR] [backend.data_setup.milvus_setup] Failed to push to app '{app_name}'
#                 \t{str(e)}
#             """)

#     def __embed_query(self, query: str):
#         embedded_text = self.__embedding_model.embed_query(query)
#         return embedded_text

#     def __retrieve_query(
#             self,
#             app_name: str,
#             embedded_query: list, 
#             top_k: int = 50
#         ):
#         query = Query(
#             query_vector = {"query_vector": embedded_query},
#             hits = top_k,
#             ranking = RankProfile(name = "default")
#         )

#         response = self.__vespa.query(body = query, schema = app_name)
#         return response.hits

#     def execute(
#             self,
#             dataset_name: str,
#             input_dataset_path: str = "vector_database_tests/encoded_dataset",
#             input_questions_path: str = "vector_database_tests/generated_questions.jsonl"
#         ):
#         input_dataset_path = os.path.join(input_dataset_path, dataset_name.replace("/", "-") + ".jsonl")

#         with open(input_dataset_path, 'r', encoding = "utf-8") as f:
#             for line in f:
#                 record = json.loads(line)
#                 self.__push_to_vespa(self.__app_name, record["embedding"])
#                 break

#         record_time = []
#         with open(input_questions_path, 'r', encoding = "utf-8") as f:
#             questions = json.load(f)[dataset_name]
#             for question in questions:
#                 embedded_question = self.__embed_query(question)
#                 start_time = time.perf_counter()
#                 self.__retrieve_query(self.__app_name, embedded_question)
#                 end_time = time.perf_counter()
#                 record_time.append((end_time - start_time) * 1000)
#                 break
        
#         print ("STOP!!")
#         return
#         record_time.sort()
#         n = len(record_time)

#         print("\n\n\nVespa vector DB latency report:")
#         print(f"Avg: {statistics.mean(record_time):.2f} ms")
#         print(f"p50: {record_time[int(0.50 * n)]:.2f} ms")
#         print(f"p95: {record_time[int(0.95 * n)]:.2f} ms")
#         print(f"p99: {record_time[int(0.99 * n)]:.2f} ms")


# if __name__ == "__main__":
#     dataset_name = "gamino/wiki_medical_terms"
#     embedding_model = "all-MiniLM-L6-v2"
#     embedding_dimension = 384

#     vespa_setup = VespaSetup(
#         embedding_model = embedding_model,
#         embedding_dimension = embedding_dimension
#     )
#     vespa_setup.execute(dataset_name)





import requests, random, time

VESPA_URL = "http://la-vespa:8080"

vectors = [
    ([random.random() for _ in range(384)], "Hello world"),
    ([random.random() for _ in range(384)], "Vector DB test"),
    ([random.random() for _ in range(384)], "Manual vector example")
]

for vec, text in vectors:
    doc_id = text.replace(" ", "_")
    doc = {"fields": {"text": text, "embedding": vec}}
    url = f"{VESPA_URL}/document/v1/mynamespace/vector/docid/{doc_id}"
    resp = requests.post(url, json=doc)
    print(f"Fed document '{text}': {resp.status_code}")

time.sleep(2)

query_vector = [random.random() for _ in range(384)]

# Format the tensor as a string in the proper format
tensor_str = ",".join([f"{i}:{v}" for i, v in enumerate(query_vector)])

query_body = {
    "yql": "select * from vector where {targetHits:3}nearestNeighbor(embedding, query_embedding)",
    "input.query(query_embedding)": query_vector,  # Just pass the list directly
    "ranking.profile": "default",
    "hits": 3
}

resp = requests.post(f"{VESPA_URL}/search/", json=query_body)
res_json = resp.json()

hits = res_json.get("root", {}).get("children", [])
if not hits:
    print("No documents retrieved. Something is still wrong.")
    if "errors" in res_json.get("root", {}):
        print(f"Errors: {res_json['root']['errors']}")
else:
    print("\nTop results ranked by similarity:")
    for hit in hits:
        doc_id = hit.get("id")
        score = hit.get("relevance")
        fields = hit.get("fields", {})
        print(f"DocID: {doc_id}, Score: {score:.4f}, Text: {fields.get('text')}")