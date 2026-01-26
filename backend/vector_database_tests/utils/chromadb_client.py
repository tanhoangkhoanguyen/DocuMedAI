import random
import chromadb

client = chromadb.Client()

collection = client.create_collection(
    name="vectors",
    metadata={"hnsw:space": "cosine"}
)


# 384-dim vectors
vectors = [[random.random() for _ in range(384)] for _ in range(3)]

collection.add(
    embeddings=vectors,
    documents=["doc A", "doc B", "doc C"],  # optional labels
    ids=["a", "b", "c"]
)

query_vec = [random.random() for _ in range(384)]

results = collection.query(
    query_embeddings=[query_vec],
    n_results=2
)

print(results)
