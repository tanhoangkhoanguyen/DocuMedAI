import sys, warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from qdrant_client import QdrantClient as SDKQdrantClient
from qdrant_client.http.models import VectorParams, Distance, PointStruct
from typing import List

QDRANT_URL = "http://la-qdrant:6333"

class QdrantClient:
    def __init__(
            self, 
            embedding_model: str,
            embedding_dimension: int
        ):
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__embedding_dimension = embedding_dimension
        self.__client = SDKQdrantClient(url = QDRANT_URL)

    def list_collections(self) -> List[str]:
        try:
            collections_info = self.__client.get_collections()
            collection_names = [collection.name for collection in collections_info.collections]
            return collection_names
        except Exception as e:
            print(f"""
                  [ERROR] [backend.vector_databases_tests.utils.qdrant_client] Failed to list collections
                  \t{str(e)}
                """)
            return []
    
    def delete_collection(
            self,
            collection_name: str
        ):
        try:
            collection_names = self.list_collections()
            if collection_name in collection_names:
                self.__client.delete_collection(collection_name)
                print(f"""
                    [INFO] [backend.vector_databases_tests.utils.qdrant_client] Deleted collection '{collection_name}'
                """)
            else:
                print(f"""
                    [INFO] [backend.vector_databases_tests.utils.qdrant_client] Collection '{collection_name}' doesnt exist
                """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.qdrant_client] Failed to delete collection '{collection_name}'
                \t{str(e)}
            """)
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
                )
            )
            print(f"""
                [INFO] [backend.vector_databases_tests.utils.qdrant_client] Created collection '{collection_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.qdrant_client] Failed to create collection '{collection_name}'
                \t{str(e)}
            """)
            raise

    def embed_query(
            self,
            query: str
        ):
        embedded_query = self.__embedding_model.embed_query(query)
        return embedded_query

    def push_to_collection(
            self,
            collection_name: str,
            ids: List[str],
            queries,
            embedded_queries
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

            self.__client.upsert(
                collection_name = collection_name,
                points = points
            )
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.qdrant_client] Failed to push to collection '{collection_name}'
                \t{str(e)}
            """)

    def retrieve_query(
            self,
            collection_name: str,
            embedded_query,
            top_k: int = 50
        ):
        try:
            response = self.__client.query_points(
                collection_name = collection_name,
                query = embedded_query,
                limit = top_k,
                with_payload = True,
                with_vectors = False
            )
            return response
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.qdrant_client] Failed to retrieve from collection '{collection_name}'
                \t{str(e)}
            """)
            return []

    def close(self):
        self.__client.close()