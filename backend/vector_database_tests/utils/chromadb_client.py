import sys, chromadb, warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from typing import List

HASH_BASE = 257
MODULO = 10**9 + 7

class ChromadbClient:
    def __init__(
            self, 
            embedding_model: str,
            embedding_dimension: int
        ):
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__embedding_dimension = embedding_dimension
        self.__chromadb_client = chromadb.PersistentClient(
                settings = chromadb.Settings(
                    persist_directory = "/backend/"
                )
            )
        self.__current_collection = None
        self.__collection = None

    def list_collections(self) -> List[str]:
        try:
            collections = self.__chromadb_client.list_collections()
            return [collection.name for collection in collections]
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.chromadb_client] Failed to list collections
                \t{str(e)}
            """)
            raise

    def delete_collection(
            self,
            collection_name: str
        ):
        try:
            if collection_name in self.list_collections():
                self.__chromadb_client.delete_collection(collection_name)
                print(f"""
                    [INFO] [backend.vector_databases_tests.utils.chromadb_client] Deleted collection '{collection_name}'
                """)
            else:
                print(f"""
                    [INFO] [backend.vector_databases_tests.utils.chromadb_client] Collection '{collection_name}' doesnt exist
                """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.chromadb_client] Failed to delete collection '{collection_name}'
                \t{str(e)}
            """)
            raise

    def create_collection(
            self,
            collection_name: str
        ):
        try:
            self.delete_collection(collection_name)
            self.__chromadb_client.create_collection(
                name = collection_name,
                metadata = {"hnsw:space": "cosine"}
            )
            print(f"""
                [INFO] [backend.vector_databases_tests.utils.chromadb_client] Created collection '{collection_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.chromadb_client] Failed to create collection '{collection_name}'
                \t{str(e)}
            """)
            raise
    
    def bind_collection(
            self,
            collection_name: str
        ):
        if self.__current_collection == collection_name:
            return
        if collection_name not in self.list_collections():
            raise ValueError(f"""
                [ERROR] [backend.vector_databases_tests.utils.chromadb_client] Collection '{collection_name}' does not exist
            """)
        self.__collection = self.__chromadb_client.get_collection(collection_name)
        self.__current_collection = collection_name
    
    def count_collection(
            self, 
            collection_name: str
        ) -> int:
        self.bind_collection(collection_name)
        return self.__collection.count()

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
            self.bind_collection(collection_name)
            self.__collection.add(
                embeddings = embedded_queries,
                documents = queries,
                ids = ids
            )
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.chromadb_client] Failed to push to collection '{collection_name}'
                \t{str(e)}
            """)

    def retrieve_query(
            self,
            collection_name: str,
            embedded_query,
            top_k: int = 50
        ):
        try:
            self.bind_collection(collection_name)
            response = self.__collection.query(
                query_embeddings = [embedded_query],
                n_results = top_k
            )
            return response
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.chromadb_client] Failed to retrieve from collection '{collection_name}'
                \t{str(e)}
            """)
            return []