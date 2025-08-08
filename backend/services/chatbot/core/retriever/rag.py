import os
import warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv

from langchain_community.embeddings import HuggingFaceEmbeddings
from qdrant_client import QdrantClient


class Ragger:
    def __init__(self,
                 embedding_model:str="sentence-transformers/all-MiniLM-L6-v2"):
        try:
            self.__qdrant_url = os.getenv("QDRANT_URL")
            self.__qdrant_api_key = os.getenv("QDRANT_API_KEY")
        except Exception as e:
            print(f"[ERROR] From Ragger init: {str(e)}")
            print(f"Terminated")
            exit(1)

        self.embedder = HuggingFaceEmbeddings(model_name=embedding_model)

        try:
            self.qdrant_client = QdrantClient(
                url=self.__qdrant_url,
                api_key=self.__qdrant_api_key
            )
        except:
            print(f"[ERROR] From Ragger's vector database connection")
            print(f"Terminated")
            exit(1)
    
    async def retrieve_single_query(self, query, collection_name, top_k:int=3):
        embedded_query = self.embedder.embed_query(query)
        try:
            results = self.qdrant_client.search(
                collection_name=collection_name,
                query_vector=embedded_query,
                limit=top_k,
                with_payload=True,
                with_vectors=False
            )
            return results
        except Exception as e:
            print(f"[ERROR] Error in retrieve search results")
            return []
    
    def close(self):
        self.qdrant_client.close()

        