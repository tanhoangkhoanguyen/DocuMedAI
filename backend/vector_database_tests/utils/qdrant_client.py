from logger import get_logger

import torch, warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from qdrant_client import QdrantClient as SDKQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    HnswConfigDiff,
    KeywordIndexParams,
    MatchValue,
    OptimizersConfigDiff,
    PayloadSchemaType,
    PointStruct,
    SearchParams,
)
from typing import Any, Dict, List, Optional

_LOGGER = get_logger(
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
            _LOGGER.error(f"Failed to list collections\n\t{str(e)}")
            return []
    
    def collection_exists(
            self,
            collection_name: str,
        ) -> bool:
        try:
            return self.__client.collection_exists(collection_name)
        except Exception as e:
            _LOGGER.error(f"Failed to check collection '{collection_name}'\n\t{str(e)}")
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
            _LOGGER.error(f"Failed to count points in '{collection_name}'\n\t{str(e)}")
            return 0

    def delete_collection(
            self,
            collection_name: str
        ):
        try:
            if not self.collection_exists(collection_name):
                _LOGGER.info(f"Collection '{collection_name}' doesnt exist")
                return
            
            self.__client.delete_collection(collection_name)
            _LOGGER.info(f"Deleted collection '{collection_name}'")
        except Exception as e:
            _LOGGER.error(f"Failed to delete collection '{collection_name}'\n\t{str(e)}")
            raise

    def create_collection(
            self, 
            collection_name: str,
            payload_indexes: Optional[List[str]] = None,
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
            for field in (payload_indexes or []):
                # user_id is the tenant key in the shared multi-tenant collection:
                # is_tenant=True tells Qdrant to co-locate each tenant's points on
                # disk, so per-user filtered search stays fast as the collection grows.
                if field == "user_id":
                    field_schema = KeywordIndexParams(type = "keyword", is_tenant = True)
                else:
                    field_schema = PayloadSchemaType.KEYWORD
                self.__client.create_payload_index(
                    collection_name = collection_name,
                    field_name = field,
                    field_schema = field_schema,
                )
            _LOGGER.info(f"Created collection '{collection_name}'")
        except Exception as e:
            _LOGGER.error(f"Failed to create collection '{collection_name}'\n\t{str(e)}")
            raise

    @staticmethod
    def __user_doc_filter(user_id: str, doc_id: Optional[str] = None) -> Filter:
        must = [FieldCondition(key = "user_id", match = MatchValue(value = user_id))]
        if doc_id:
            must.append(FieldCondition(key = "doc_id", match = MatchValue(value = doc_id)))
        return Filter(must = must)

    def delete_by_doc(
            self,
            collection_name: str,
            user_id: str,
            doc_id: str,
        ):
        """
        Delete all points for one (user_id, doc_id). user_id in the selector prevents
        a cross-user purge. Used for idempotent re-ingestion and document deletion.
        """
        try:
            if not self.collection_exists(collection_name):
                return
            self.__client.delete(
                collection_name = collection_name,
                points_selector = self.__user_doc_filter(user_id, doc_id),
            )
        except Exception as e:
            _LOGGER.error(f"Failed to delete doc '{doc_id}' from '{collection_name}'\n\t{str(e)}")
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
            queries: Optional[List[str]],
            embedded_queries: List[List[float]],
            payloads: Optional[List[Dict[str, Any]]] = None,
        ):
        try:
            points = []
            for idx in range(len(ids)):
                id = ids[idx]
                embedded_query = embedded_queries[idx]
                payload = payloads[idx] if payloads is not None else {"query": queries[idx]}
                points.append(
                    PointStruct(
                        id = id,
                        vector = embedded_query,
                        payload = payload,
                ))

            self.__client.upload_points(
                collection_name = collection_name,
                points = points,
                wait = True    # Prevent server-side ingest error
            )
        except Exception as e:
            _LOGGER.error(f"Failed to push to collection '{collection_name}'\n\t{str(e)}")
            raise

    def retrieve_query(
            self,
            collection_name: str,
            embedded_query,
            top_k: int = 50,
            search_param: int = 64,
            user_id: Optional[str] = None,
            doc_id: Optional[str] = None,
            with_payload: bool = False,
        ):
        try:
            query_filter = self.__user_doc_filter(user_id, doc_id) if user_id else None
            resp = self.__client.query_points(
                collection_name = collection_name,
                query = embedded_query,
                query_filter = query_filter,
                limit = top_k,
                search_params = SearchParams(hnsw_ef = search_param),
                with_payload = with_payload,
                with_vectors = False
            )
            return resp
        except Exception as e:
            _LOGGER.error(f"Failed to retrieve from collection '{collection_name}'\n\t{str(e)}")
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
    def _payload_texts_from_response(resp) -> List[str]:
        if not resp:
            return []
        points = getattr(resp, "points", None)
        if points is None and isinstance(resp, dict):
            points = resp.get("points")
        if not points:
            return []
        out: List[str] = []
        for p in points:
            payload = getattr(p, "payload", None) or {}
            if not isinstance(payload, dict):
                payload = {}
            text = payload.get("text") or payload.get("query") or ""
            if text:
                out.append(str(text))
        return out

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
    cache_key = (embedding_model, embedding_dimension)
    if cache_key not in _QDRANT_DICT:
        _QDRANT_DICT[cache_key] = QdrantClient(
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension,
        )
    return _QDRANT_DICT[cache_key]