from logger import get_logger

import torch, chromadb, warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from typing import List

LOGGER = get_logger(__name__)

class ChromadbClient:
    def __init__(
            self, 
            embedding_model: str,
            embedding_dimension: int
        ):
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.__embedding_model = HuggingFaceEmbeddings(
                model_name = embedding_model,
                model_kwargs = {"device": device}
            )
        self.__embedding_dimension = embedding_dimension
        self.__chromadb_client = chromadb.PersistentClient(
                settings = chromadb.Settings(
                    persist_directory = "/backend/"
                )
            )
        self.__current_collection = None
        self._chroma_client = None

    def embed_query(
            self,
            query: str
        ):
        embedded_query = self.__embedding_model.embed_query(query)
        return embedded_query

    def list_collections(self):
        try:
            collections = self.__chromadb_client.list_collections()
            return [collection.name for collection in collections]
        except Exception as e:
            LOGGER.error(f"Failed to list collections\n\t{str(e)}")
            raise
    
    def collection_exists(
            self,
            collection_name: str,
        ):
        try:
            if collection_name in self.list_collections():
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Failed to check '{collection_name}' existence\n\t{e}")
            return False

    def delete_collection(
            self,
            collection_name: str
        ):
        try:
            if not self.collection_exists(collection_name):
                LOGGER.info(f"Collection '{collection_name}' doesnt exist")
                return
            
            self.__chromadb_client.delete_collection(collection_name)
            LOGGER.info(f"Deleted collection '{collection_name}'")
        except Exception as e:
            LOGGER.error(f"Failed to delete collection '{collection_name}'\n\t{str(e)}")
            raise

    def create_collection(
            self,
            collection_name: str
        ):
        try:
            self.delete_collection(collection_name)
            self.__chromadb_client.create_collection(
                name = collection_name,
                metadata = {
                    "hnsw:space": "cosine",
                    "hnsw:construction_ef": 200,
                    "hnsw:M": 64
                }
            )
            LOGGER.info(f"Created collection '{collection_name}'")
        except Exception as e:
            LOGGER.error(f"Failed to create collection '{collection_name}'\n\t{str(e)}")
            raise
    
    def bind_collection(
            self,
            collection_name: str
        ):
        if self.__current_collection == collection_name:
            return
        if not self.collection_exists(collection_name):
            raise ValueError(f"Collection '{collection_name}' does not exist")
        self._chroma_client = self.__chromadb_client.get_collection(collection_name)
        self.__current_collection = collection_name
    
    def push_documents(
            self,
            collection_name: str,
            ids: List[str],
            queries: List[str],
            embedded_queries: List[List[float]]
        ):
        try:
            self.bind_collection(collection_name)
            self._chroma_client.add(
                embeddings = embedded_queries,
                documents = queries,
                ids = ids
            )
        except Exception as e:
            LOGGER.error(f"Failed to push to collection '{collection_name}'\n\t{str(e)}")

    def retrieve_query(
            self,
            collection_name: str,
            embedded_query: List[float],
            top_k: int = 50
        ):
        try:
            self.bind_collection(collection_name)
            resp = self._chroma_client.query(
                query_embeddings = [embedded_query],
                n_results = top_k
            )
            return resp
        except Exception as e:
            LOGGER.error(f"Failed to retrieve from collection '{collection_name}'\n\t{str(e)}")
            return []