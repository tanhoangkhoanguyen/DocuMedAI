import os
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http.models import VectorParams, Distance, PointStruct

load_dotenv()

def qdrant_demo():
    print("===== DEMO QDRANT =====")

    client = QdrantClient(
        url=os.getenv("QDRANT_URL"),
        api_key=os.getenv("QDRANT_API_KEY"),
    )

    # 1. (Re)create a collection
    client.recreate_collection(
        collection_name="my_collection",
        vectors_config=VectorParams(size=128, distance=Distance.COSINE),
    )

    # 2. Insert vector points
    points = [
        PointStruct(id=1, vector=[0.1] * 128),
        PointStruct(id=2, vector=[0.2] * 128),
        PointStruct(id=2, vector=[0.2] * 128),
        PointStruct(id=2, vector=[0.2] * 128),
        PointStruct(id=2, vector=[0.2] * 128),
    ]
    client.upsert(collection_name="my_collection", points=points)

    # 3. Query k-NN
    hits = client.search(
        collection_name="my_collection",
        query_vector=[0.1] * 128,
        limit=3
    )
    print("Search results:")
    print(hits)

    print("===== DEMO QDRANT DONE =====")


if __name__ == "__main__":
    qdrant_demo()
    print("===== DEMO QDRANT DONE =====")