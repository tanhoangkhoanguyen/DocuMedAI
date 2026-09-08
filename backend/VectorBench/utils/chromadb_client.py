from logger import get_logger

import torch, chromadb, warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from typing import List

_LOGGER = get_logger(
    name = "Chromadb_client",
    level = "INFO"
)
_CHROMADB_DICT = {}
CHROMA_HOST = "la-chroma"
CHROMA_PORT = 8000

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
        self.__chromadb_client = chromadb.HttpClient(
                host = CHROMA_HOST,
                port = CHROMA_PORT,
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
            _LOGGER.error(f"Failed to list collections\n\t{str(e)}")
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
            _LOGGER.error(f"Failed to check '{collection_name}' existence\n\t{e}")
            return False

    def delete_collection(
            self,
            collection_name: str
        ):
        try:
            if not self.collection_exists(collection_name):
                _LOGGER.info(f"Collection '{collection_name}' doesnt exist")
                return
            
            self.__chromadb_client.delete_collection(collection_name)
            _LOGGER.info(f"Deleted collection '{collection_name}'")
        except Exception as e:
            _LOGGER.error(f"Failed to delete collection '{collection_name}'\n\t{str(e)}")
            raise

    def create_collection(
            self,
            collection_name: str,
            search_ef: int = 64,
        ):
        try:
            self.delete_collection(collection_name)
            self.__chromadb_client.create_collection(
                name = collection_name,
                metadata = {
                    "hnsw:space": "cosine",
                    "hnsw:construction_ef": 200,
                    "hnsw:M": 64,
                    "hnsw:search_ef": search_ef,
                }
            )
            _LOGGER.info(f"Created collection '{collection_name}'")
        except Exception as e:
            _LOGGER.error(f"Failed to create collection '{collection_name}'\n\t{str(e)}")
            raise

    def set_search_ef(self, collection_name: str, search_ef: int):
        """Adjust query-time HNSW ef without re-uploading (used by the sweep)."""
        try:
            self.bind_collection(collection_name)
            self._chroma_client.modify(metadata = {"hnsw:search_ef": search_ef})
            self.__current_collection = None  # force re-bind so the new ef takes effect
        except Exception as e:
            _LOGGER.error(f"Failed to set search_ef on '{collection_name}'\n\t{str(e)}")
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
            _LOGGER.error(f"Failed to push to collection '{collection_name}'\n\t{str(e)}")

    def retrieve_query(
            self,
            collection_name: str,
            embedded_query: List[float],
            top_k: int = 50,
            search_param: int = None,
        ):
        # Chroma's query-time ef is a collection property (hnsw:search_ef); use
        # set_search_ef() to change it. `search_param` is accepted for a uniform signature.
        try:
            self.bind_collection(collection_name)
            resp = self._chroma_client.query(
                query_embeddings = [embedded_query],
                n_results = top_k,
                include = []  # ids are always returned; skip documents/distances/embeddings
            )
            return resp
        except Exception as e:
            _LOGGER.error(f"Failed to retrieve from collection '{collection_name}'\n\t{str(e)}")
            return []

    def retrieve_ids(
            self,
            collection_name: str,
            embedded_query: List[float],
            top_k: int = 50,
            search_param: int = None,
        ) -> List[str]:
        """Return only the ordered list of ids (for recall@k)."""
        resp = self.retrieve_query(collection_name, embedded_query, top_k, search_param)
        if not resp:
            return []
        ids = resp.get("ids") or [[]]
        return list(ids[0])


def get_chromadb_client(
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        embedding_dimension: int = 384
    ):
    if not embedding_model in _CHROMADB_DICT:
        _CHROMADB_DICT[embedding_model] = ChromadbClient(
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension
        )
    return _CHROMADB_DICT[embedding_model]