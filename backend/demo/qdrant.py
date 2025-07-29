# import os, re
# from dotenv import load_dotenv, find_dotenv
# from qdrant_client import QdrantClient
# from qdrant_client.http.models import VectorParams, Distance, PointStruct
# from qdrant_client.http import models
# from langchain.document_loaders import PyPDFLoader
# from langchain.schema import Document
# from langchain.text_splitter import RecursiveCharacterTextSplitter
# from langchain.embeddings import OpenAIEmbeddings
# from qdrant_client import QdrantClient
# from qdrant_client.http.models import PointStruct
# import uuid


# load_dotenv(find_dotenv())


# def load_clean_documents(folder_path = "demo/qdrant_pdf"):
#     cleaned_docs = []

#     for file_name in os.listdir(folder_path):
#         if not file_name.endswith(".pdf"):
#             continue

#         file_path = os.path.join(folder_path, file_name)
#         loader = PyPDFLoader(file_path)
#         raw_docs = loader.load()

#         for doc in raw_docs:
#             clean = doc.page_content.encode('ascii', errors='ignore').decode()
#             clean = re.sub(r'\s+', ' ', clean).replace('.', '').strip()
#             cleaned_docs.append(Document(page_content=clean, metadata=doc.metadata))

#     return cleaned_docs


# def split_documents(documents):
#     splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
#         chunk_size=200,
#         chunk_overlap=50
#     )
#     return splitter.split_documents(documents)


# def upload_to_qdrant(chunks, collection_name="lawAdvisory"):
#     embeddings = OpenAIEmbeddings()
#     qdrant = QdrantClient(host="localhost", port=6333)  # adjust if needed

#     for idx, chunk in enumerate(chunks):
#         vector = embeddings.embed_query(chunk.page_content)
#         qdrant.upsert(
#             collection_name=collection_name,
#             points=[
#                 PointStruct(
#                     id=str(uuid.uuid4()),
#                     vector=vector,
#                     payload={
#                         "text": chunk.page_content,
#                         "metadata": chunk.metadata
#                     }
#                 )
#             ]
#         )


# def qdrant_demo():
#     print("===== DEMO QDRANT =====")

#     client = QdrantClient(
#         url=os.getenv("QDRANT_URL"),
#         api_key=os.getenv("QDRANT_API_KEY"),
#     )

#     collection_config = models.VectorParams(
#         size = 1536, # dimensional-vector
#         distance = models.Distance.COSINE # similarity metric
#     )

#     client.create_collection(
#         collection_name = "lawAdvisory",
#         vectors_config = collection_config
#     )

#     doc = load_clean_documents()
#     doc2 = split_documents(doc)
#     upload_to_qdrant(doc2)

#     # client.delete_collection(collection_name = "lawAdvisory")

#     client.close()


# if __name__ == "__main__":
#     qdrant_demo()
#     print("===== DEMO QDRANT DONE =====")
import os, re
from dotenv import load_dotenv, find_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http.models import VectorParams, Distance, PointStruct
from qdrant_client.http import models
from langchain.document_loaders import PyPDFLoader
from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.embeddings import OpenAIEmbeddings
import uuid


load_dotenv(find_dotenv())


def load_clean_documents(folder_path="demo/qdrant_pdf"):
    cleaned_docs = []

    for file_name in os.listdir(folder_path):
        if not file_name.endswith(".pdf"):
            continue

        file_path = os.path.join(folder_path, file_name)
        loader = PyPDFLoader(file_path)
        raw_docs = loader.load()

        for doc in raw_docs:
            clean = doc.page_content.encode('ascii', errors='ignore').decode()
            clean = re.sub(r'\s+', ' ', clean).replace('.', '').strip()
            cleaned_docs.append(Document(page_content=clean, metadata=doc.metadata))

    return cleaned_docs


def split_documents(documents):
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        chunk_size=200,
        chunk_overlap=50
    )
    return splitter.split_documents(documents)


def upload_to_qdrant(chunks, client, collection_name="lawAdvisory"):
    """Upload chunks to Qdrant using the same client instance"""
    embeddings = OpenAIEmbeddings()

    # Process chunks in batches for better performance
    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        points = []
        
        for chunk in batch:
            vector = embeddings.embed_query(chunk.page_content)
            points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload={
                        "text": chunk.page_content,
                        "metadata": chunk.metadata
                    }
                )
            )
        
        client.upsert(
            collection_name=collection_name,
            points=points
        )
        print(f"Uploaded batch {i//batch_size + 1}/{(len(chunks) + batch_size - 1)//batch_size}")


def query_qdrant(query, collection_name="lawAdvisory", top_k=3):
    """Query Qdrant using the same client configuration as the main demo"""
    embeddings = OpenAIEmbeddings()
    
    # Use the same client configuration as in qdrant_demo()
    client = QdrantClient(
        url=os.getenv("QDRANT_URL"),
        api_key=os.getenv("QDRANT_API_KEY"),
    )
    
    try:
        query_vector = embeddings.embed_query(query)

        results = client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=top_k
        )

        return [res.payload['text'] for res in results]
    
    except Exception as e:
        print(f"Error querying Qdrant: {e}")
        return []
    
    finally:
        client.close()
    

def qdrant_demo():
    print("===== DEMO QDRANT =====")

    # Use consistent client configuration
    client = QdrantClient(
        url=os.getenv("QDRANT_URL"),
        api_key=os.getenv("QDRANT_API_KEY"),
    )
    
    # # Test connection
    # try:
    #     collections = client.get_collections()
    #     print("Successfully connected to Qdrant")
    # except Exception as e:
    #     print(f"Failed to connect to Qdrant: {e}")
    #     return

    # collection_name = "lawAdvisory"
    
    # # Check if collection exists and delete if it does
    # try:
    #     client.get_collection(collection_name)
    #     print(f"Collection '{collection_name}' exists, deleting...")
    #     client.delete_collection(collection_name)
    # except:
    #     print(f"Collection '{collection_name}' doesn't exist, creating new one...")

    # # Create collection with proper configuration
    # collection_config = models.VectorParams(
    #     size=1536,  # OpenAI embeddings dimension
    #     distance=models.Distance.COSINE
    # )

    # client.create_collection(
    #     collection_name=collection_name,
    #     vectors_config=collection_config
    # )
    # print(f"Created collection '{collection_name}'")

    # # Load, split, and upload documents
    # print("Loading documents...")
    # doc = load_clean_documents()
    # print(f"Loaded {len(doc)} documents")
    
    # print("Splitting documents...")
    # doc2 = split_documents(doc)
    # print(f"Created {len(doc2)} chunks")
    
    # print("Uploading to Qdrant...")
    # upload_to_qdrant(doc2, client, collection_name)
    # print("Upload completed!")

    # # Verify upload
    # collection_info = client.get_collection(collection_name)
    # print(f"Collection now contains {collection_info.points_count} points")

    print("\n===== QUERYING =====")
    resp = query_qdrant("How long will I be sentenced if I break into a house?")
    print("Query results:")
    for i, result in enumerate(resp, 1):
        print(f"{i}. {result}")
        print ()

    client.close()


if __name__ == "__main__":
    qdrant_demo()