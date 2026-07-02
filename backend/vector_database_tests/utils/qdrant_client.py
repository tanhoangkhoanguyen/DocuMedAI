from logger import get_logger

import torch, warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from qdrant_client import QdrantClient as SDKQdrantClient
from qdrant_client.http.models import VectorParams, Distance, PointStruct, HnswConfigDiff, OptimizersConfigDiff, SearchParams
from typing import Any, List

LOGGER = get_logger(
    name = "Qdrant_client",
    level = "INFO"
)
_QDRANT_DICT = {}
QDRANT_URL = "http://la-qdrant:6333"

class QdrantClient:
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
        self.__client = SDKQdrantClient(url = QDRANT_URL)

    def list_collections(self) -> List[str]:
        try:
            collections_info = self.__client.get_collections()
            collection_names = [collection.name for collection in collections_info.collections]
            return collection_names
        except Exception as e:
            LOGGER.error(f"Failed to list collections\n\t{str(e)}")
            return []
    
    def collection_exists(
            self,
            collection_name: str,
        ) -> bool:
        try:
            return self.__client.collection_exists(collection_name)
        except Exception as e:
            LOGGER.error(f"Failed to check collection '{collection_name}'\n\t{str(e)}")
            return False
    
    def count_points(
            self,
            collection_name: str
        ) -> int:
        """Return the number of points in a collection (0 if it doesnt exist)."""
        try:
            if not self.collection_exists(collection_name):
                return 0
            return self.__client.count(collection_name = collection_name, exact = True).count
        except Exception as e:
            LOGGER.error(f"Failed to count points in '{collection_name}'\n\t{str(e)}")
            return 0

    def delete_collection(
            self,
            collection_name: str
        ):
        try:
            if not self.collection_exists(collection_name):
                LOGGER.info(f"Collection '{collection_name}' doesnt exist")
                return
            
            self.__client.delete_collection(collection_name)
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
            self.__client.create_collection(
                collection_name = collection_name,
                vectors_config = VectorParams(
                    size = self.__embedding_dimension, 
                    distance = Distance.COSINE
                ),
                hnsw_config = HnswConfigDiff(
                    m = 64,
                    ef_construct = 200
                ),
                optimizers_config = OptimizersConfigDiff(
                    indexing_threshold = 20000
                )
            )
            LOGGER.info(f"Created collection '{collection_name}'")
        except Exception as e:
            LOGGER.error(f"Failed to create collection '{collection_name}'\n\t{str(e)}")
            raise

    def embed_query(
            self,
            query: str
        ):
        embedded_query = self.__embedding_model.embed_query(query)
        return embedded_query

    def push_documents(
            self,
            collection_name: str,
            ids: List[str],
            queries: List[str],
            embedded_queries: List[List[float]]
        ):
        try:
            points = []
            for idx in range(len(ids)):
                id = ids[idx]
                query = queries[idx]
                embedded_query = embedded_queries[idx]
                points.append(
                    PointStruct(
                        id = id,
                        vector = embedded_query,
                        payload = {"query": query}
                ))

            self.__client.upload_points(
                collection_name = collection_name,
                points = points
            )
        except Exception as e:
            LOGGER.error(f"Failed to push to collection '{collection_name}'\n\t{str(e)}")

    def retrieve_query(
            self,
            collection_name: str,
            embedded_query,
            top_k: int = 50,
            search_param: int = 64,
        ):
        try:
            resp = self.__client.query_points(
                collection_name = collection_name,
                query = embedded_query,
                limit = top_k,
                search_params = SearchParams(hnsw_ef = search_param),
                with_payload = False,
                with_vectors = False
            )
            return resp
        except Exception as e:
            LOGGER.error(f"Failed to retrieve from collection '{collection_name}'\n\t{str(e)}")
            return []

    def retrieve_ids(
            self,
            collection_name: str,
            embedded_query,
            top_k: int = 50,
            search_param: int = 64,
        ) -> List[str]:
        """Return only the ordered list of point ids (for recall@k)."""
        resp = self.retrieve_query(collection_name, embedded_query, top_k, search_param)
        points = getattr(resp, "points", None) or []
        return [str(p.id) for p in points]

    @staticmethod
    def get_top_scored_payload(
            resp: Any,
            score_threshold: float,
        ) -> str:
        if not resp:
            return ""
        points = getattr(resp, "points", None)
        if points is None and isinstance(resp, dict):
            points = resp.get("points")
        if not points:
            return ""
        p0 = points[0]
        score = getattr(p0, "score", None)
        if score is None and isinstance(p0, dict):
            score = p0.get("score", 0.0)
        if score is None or float(score) < score_threshold:
            return ""
        payload = getattr(p0, "payload", None) or {}
        if not isinstance(payload, dict):
            payload = {}
        text = payload.get("query") or payload.get("text") or ""
        return str(text).strip() if text else ""

    def close(self):
        self.__client.close()

def get_qdrant_client(
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        embedding_dimension: int = 384
    ):
    if embedding_model not in _QDRANT_DICT:
        _QDRANT_DICT[embedding_model] = QdrantClient(
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension,
        )
    return _QDRANT_DICT[embedding_model]