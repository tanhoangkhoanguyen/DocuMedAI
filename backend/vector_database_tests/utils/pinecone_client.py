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
        self.__session = requests.Session()
        self.__session.headers.update({"Api-Key": PINECONE_API_KEY})

    def list_collections(self) -> List[str]:
        try:
            response = self.__session.get(f"{PINECONE_CONTROL_URL}/indexes")
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
            if collection_name in self.list_collections():
                response = self.__session.delete(f"{PINECONE_CONTROL_URL}/indexes/{collection_name}")
                response.raise_for_status()
                print(f"""
                    [INFO] [backend.vector_databases_tests.utils.pinecone_client] Deleted collection '{collection_name}'
                """)
            else:
                print(f"""
                    [INFO] [backend.vector_databases_tests.utils.pinecone_client] Collection '{collection_name}' doesnt exist
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
            self.delete_collection(collection_name)
            response = self.__session.post(
                f"{PINECONE_CONTROL_URL}/indexes",
                json = {
                    "name": collection_name,
                    "dimension": self.__embedding_dimension,
                    "metric": "cosine",
                    "spec": {
                        "serverless": {
                            "cloud": "aws",
                            "region": "us-east-1"
                        }
                    }
                }
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
            # Assuming that the collection_name always exist
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
            batch_size = 100
            for i in range (0, len(vectors), batch_size):
                batch = vectors[i : i + batch_size]
                response = self.__session.post(
                    f"{PINECONE_DATA_URL}/vectors/upsert",
                    headers = {"X-Pinecone-Index-Name": collection_name},
                    json = {"vectors": batch}
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
            embedded_query,
            top_k: int = 10
        ):
        try:
            # Assuming that the collection_name always exist
            response = requests.post(
                f"{PINECONE_DATA_URL}/query",
                headers = {
                    'Api-Key': 'pclocal', 
                    'X-Pinecone-Index-Name': 'latencytest'
                }, 
                json = {
                    'vector': embedded_query, 
                    'topK': 1, 
                    'includeMetadata': True
                }
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.pinecone_client] Failed to retrieve from collection '{collection_name}'
                \t{str(e)}
            """)
            return []