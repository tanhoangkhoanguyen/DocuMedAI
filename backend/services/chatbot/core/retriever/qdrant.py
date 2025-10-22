import os, warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()

from langchain_huggingface import HuggingFaceEmbeddings
from qdrant_client import QdrantClient


class QdrantSearcher:
    def __init__(self, embedding_model: str):
        self.embedder = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__client = QdrantClient(
            url = os.getenv("QDRANT_URL"),
            api_key = os.getenv("QDRANT_API_KEY")
        )
    
    def retrieve_query(self, query:str, collection_name:str, top_k:int = 3):
        embedded_query = self.embedder.embed_query(query)
        results = self.__client.search(
            collection_name = collection_name,
            query_vector = embedded_query,
            limit = top_k,
            with_payload = True,
            with_vectors = False
        )
        return results
    
    def close(self):
        self.__client.close()