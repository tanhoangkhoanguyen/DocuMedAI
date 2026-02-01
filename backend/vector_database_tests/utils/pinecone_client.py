import sys, requests, warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from typing import List

PINECONE_CONTROL_URL = "http://la-pinecone:5080"
PINECONE_DATA_URL = "http://la-pinecone:5081"
PINECONE_API_KEY = "pclocal"

class PineconeClient:
    def __init__(
            self,
            embedding_model: str,
            embedding_dimension: int
        ):
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__embedding_dimension = embedding_dimension

    def list_collections(self) -> List[str]:
        try:
            response = requests.get(
                f"{PINECONE_CONTROL_URL}/indexes",
                headers = {"Api-Key": PINECONE_API_KEY}
            )
            response.raise_for_status()
            collections_info = response.json()
            collection_names = [idx["name"] for idx in collections_info.get("indexes", [])]
            return collection_names
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.pinecone_client] Failed to list collections
                \t{str(e)}
            """)
            return []
    
    def delete_collection(
            self,
            collection_name: str
        ):
        try:
            response = requests.delete(
                    f"{PINECONE_CONTROL_URL}/indexes/{collection_name}",
                    headers = {"Api-Key": PINECONE_API_KEY}
                )
            response.raise_for_status()
            print(f"""
                [INFO] [backend.vector_databases_tests.utils.pinecone_client] Deleted collection '{collection_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.pinecone_client] Failed to deleted collection '{collection_name}'
                \t{str(e)}
            """)
            raise

    def create_collection(
        self,
        collection_name: str
    ):
        try:
            existing_collections = self.list_collections()
            if collection_name in existing_collections:
                self.delete_collection(collection_name)
                
            response = requests.post(
                f"{PINECONE_CONTROL_URL}/indexes",
                json = {
                    "name": collection_name,
                    "dimension": self.__embedding_dimension,
                    "metric": "cosine",
                    "spec": {
                        "pod": {
                            "environment": "us-east-1-aws",
                            "pod_type": "p1.x1"
                        }
                    }
                },
                headers = {"Api-Key": PINECONE_API_KEY}
            )
            response.raise_for_status()            
            print(f"""
                [INFO] [backend.vector_databases_tests.utils.pinecone_client] Created collection '{collection_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.pinecone_client] Failed to create collection '{collection_name}'
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
            vectors = []
            for idx in range(len(ids)):
                id = ids[idx]
                query = queries[idx]
                embedded_query = embedded_queries[idx]
                vectors.append({
                    "id": id,
                    "values": embedded_query,
                    "metadata": {
                        "query": query
                    }
                })
            response = requests.post(
                f"{PINECONE_DATA_URL}/vectors/upsert",
                json = {"vectors": vectors},
                headers = {"Api-Key": PINECONE_API_KEY}
            )
            response.raise_for_status()
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.pinecone_client] Failed to push to collection '{collection_name}'
                \t{str(e)}
            """)

    def retrieve_query(
            self,
            collection_name: str,
            embedded_query: list,
            top_k: int = 50
        ):
        try:
            response = requests.post(
                f"{PINECONE_DATA_URL}/query",
                json = {
                    "vector": embedded_query,
                    "topK": top_k,
                    "includeMetadata": True
                },
                headers = {"Api-Key": PINECONE_API_KEY}
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.pinecone_client] Failed to retrieve from collection '{collection_name}'
                \t{str(e)}
            """)
            return []