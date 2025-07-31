import json, os, pickle, re, uuid
from dotenv import load_dotenv
from langchain.document_loaders import PyPDFLoader
from langchain.embeddings import OpenAIEmbeddings, HuggingFaceEmbeddings
from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.models import VectorParams, Distance, PointStruct

load_dotenv()

def load_cleaned_documents(sub: str = "", path = "services/chatbot/documents"):
    cleaned_docs = []
    folder_path = os.path.join(path, sub)
    storage_path = os.path.join(folder_path, "document.pkl")
    if os.path.exists(storage_path) and os.stat(storage_path).st_size > 0:
        with open(storage_path, "rb") as f:
            return pickle.load(f)
    for file_name in os.listdir(folder_path):
        if not file_name.endswith(".pdf"):
            continue
        file_path = os.path.join(folder_path, file_name)
        loader = PyPDFLoader(file_path)
        raw_docs = loader.load()
        for doc in raw_docs:
            clean = doc.page_content.encode('ascii', errors='ignore').decode()
            clean = re.sub(r'\s+', ' ', clean).replace('.', '').strip()
            cleaned_docs.append(Document(page_content = clean, metadata = doc.metadata))
    with open(storage_path, "wb") as f:
        pickle.dump(cleaned_docs, f)
    return cleaned_docs

def upload_to_qdrant(doc, client, collection_name):
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        chunk_size = 800,
        chunk_overlap = 100
    )
    chunks = splitter.split_documents(doc)
    embedding_model = HuggingFaceEmbeddings(model_name = "sentence-transformers/all-MiniLM-L6-v2")
    batch_size = 50
    total_batches = (len(chunks) + batch_size - 1) // batch_size
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        batch_texts = [chunk.page_content for chunk in batch]
        batch_vectors = embedding_model.embed_documents(batch_texts)
        points = [
            PointStruct(
                id = str(uuid.uuid4()),
                vector = vector,
                payload = {
                    "text": chunk.page_content,
                    "metadata": chunk.metadata
                }
            ) for chunk, vector in zip(batch, batch_vectors)
        ]
        client.upsert(
            collection_name = collection_name,
            points = points
        )
        print(f"Uploaded batch {i // batch_size + 1}/{total_batches} ({len(points)} chunks)")
    
def qdrant_setup():
    client = QdrantClient(
        url = os.getenv("QDRANT_URL"),
        api_key = os.getenv("QDRANT_API_KEY"),
        timeout = 120.0
    )
    
    # Test connection
    try:
        _ = client.get_collections()
        print("Successfully connected to Qdrant")
    except Exception as e:
        print(f"Failed to connect to Qdrant: {e}")
        return

    collections = ["civil_law", "criminal_law", "environmental_law", "international_law", "labor_and_employment_law"]
    for collection_name in collections:
        try:
            if client.get_collection(collection_name):
                print(f"Collection '{collection_name}' exists")
                raise PermissionError("status_code: 403")
        except:
            pass
        
        collection_config = models.VectorParams(
            size = 384, # vector dimension
            distance = models.Distance.COSINE
        )
        client.create_collection(
            collection_name = collection_name,
            vectors_config = collection_config
        )
        print(f"Created collection '{collection_name}'")
    
    print("Loading documents...")
    for collection_name in collections:
        doc = load_cleaned_documents(sub = collection_name)
        upload_to_qdrant(doc, client, collection_name)

    client.close()

if __name__ == "__main__":
    qdrant_setup()