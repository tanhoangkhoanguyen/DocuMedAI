import sys, warnings, time
warnings.filterwarnings("ignore")
from qdrant_client import QdrantClient
from qdrant_client.http.models import VectorParams, Distance, PointStruct
from langchain_community.embeddings import HuggingFaceEmbeddings

QDRANT_URL = "http://la-qdrant:6333"
COLLECTION_NAME = "qdrant_service_test"
EMBED_MODEL = "all-MiniLM-L6-v2"

class QdrantTest:
    def __init__(self):
        self.client = QdrantClient(url = QDRANT_URL)
        self.embedder = HuggingFaceEmbeddings(model_name = EMBED_MODEL)

    def create(self):
        if self.client.collection_exists(COLLECTION_NAME):
            self.client.delete_collection(COLLECTION_NAME)
        self.client.create_collection(
            collection_name = COLLECTION_NAME,
            vectors_config = VectorParams(size = 384, distance = Distance.COSINE)
        )
        sentences = [
            "I like playing football on weekends.",
            "Python is my favorite programming language.",
            "My cat loves chasing laser pointers.",
            "I enjoy long walks on the beach at sunrise."
        ]
        embeddings = self.embedder.embed_documents(sentences)
        points = [
            PointStruct(
                id = i,
                vector = embeddings[i],
                payload = {"text": sentences[i]}
            )
            for i in range(len(sentences))
        ]
        self.client.upsert(collection_name = COLLECTION_NAME, points = points)
        print(f"Uploaded {len(points)} points to collection '{COLLECTION_NAME}'.")

    def list_all(self):
        collection_info = self.client.get_collection(collection_name = COLLECTION_NAME)
        print("Number of points in collection:", collection_info.vectors_count)

    def semantic_search(self, query, top_k = 1):
        q_vec = self.embedder.embed_query(query)
        hits = self.client.query_points(
            collection_name = COLLECTION_NAME,
            query = q_vec,
            limit = top_k,
            with_payload = True
        )
        return [(h.id, h.score, h.payload) for h in hits.points]

if __name__ == "__main__":
    user_input = input("Type 'Execute' to run: ")
    if user_input != "Execute":
        sys.exit()

    start_time = time.time()
    q = "Which programming language do I enjoy?"
    qdranttest = QdrantTest()
    qdranttest.create()
    results = qdranttest.semantic_search(q)

    print("Query:", q)
    for idx, score, payload in results:
        print(f"match id = {idx}, score = {score:.4f}, text = {payload.get('text')}")
    print ("Execution time: ", time.time() - start_time, "seconds")