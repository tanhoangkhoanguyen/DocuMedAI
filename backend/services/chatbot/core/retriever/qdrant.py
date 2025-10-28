import os, warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()

from qdrant_client import QdrantClient
from services.chatbot.core.tools.helper import EmbeddingModelLoad


class QdrantSearcher:
    def __init__(self, embedding_model: str):
        self.__embedding_model = EmbeddingModelLoad.get_instance(embedding_model = embedding_model)
        self.__client = QdrantClient(
            url = os.getenv("QDRANT_URL"),
            api_key = os.getenv("QDRANT_API_KEY")
        )
    
    def retrieve_query(self, query:str, collection_name:str, top_k:int = 3):
        embedded_query = self.__embedding_model.embed_query(query)
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