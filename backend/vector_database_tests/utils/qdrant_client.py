from logger import get_logger

import torch, warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from qdrant_client import QdrantClient as SDKQdrantClient
from qdrant_client.http.models import VectorParams, Distance, PointStruct, HnswConfigDiff, OptimizersConfigDiff, SearchParams
from typing import List

LOGGER = get_logger(
    name = "Qdrant_tool",
    level = "INFO"
)
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
            top_k: int = 50
        ):
        try:
            resp = self.__client.query_points(
                collection_name = collection_name,
                query = embedded_query,
                limit = top_k,
                search_params = SearchParams(hnsw_ef = 64),
                with_payload = True,
                with_vectors = False
            )
            return resp
        except Exception as e:
            LOGGER.error(f"Failed to retrieve from collection '{collection_name}'\n\t{str(e)}")
            return []

    def close(self):
        self.__client.close()