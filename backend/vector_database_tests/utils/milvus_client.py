import sys, warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from pymilvus import connections, FieldSchema, CollectionSchema, DataType, Collection, utility
from typing import List

MILVUS_HOST = "la-milvus"
MILVUS_PORT = "19530"

class MilvusClient:
    def __init__(
            self,
            embedding_model: str,
            embedding_dimension: int,
        ):
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__embedding_dimension = embedding_dimension
        self.__milvus_client = None

        connections.connect(
            alias = "default",
            host = MILVUS_HOST,
            port = MILVUS_PORT
        )

    def list_collections(self) -> List[str]:
        try:
            collections = utility.list_collections()
            return collections
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.milvus_client] Failed to list collections
                \t{str(e)}
            """)
            return []
    
    def delete_collection(
            self,
            collection_name: str
        ):
        try:
            utility.drop_collection(collection_name)
            print(f"""
                [INFO] [backend.vector_databases_tests.utils.milvus_client] Deleted collection '{collection_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.milvus_client] Failed to deleted collection '{collection_name}'
                \t{str(e)}
            """)
            raise

    def create_collection(
            self, 
            collection_name: str
        ):
        try:
            if utility.has_collection(collection_name):
                self.delete_collection(collection_name)

            fields = [
                FieldSchema(
                    name = "id",
                    dtype = DataType.VARCHAR,
                    max_length = 64,
                    is_primary = True,
                    auto_id = False
                ),
                FieldSchema(
                    name = "embedded_query",
                    dtype = DataType.FLOAT_VECTOR,
                    dim = self.__embedding_dimension
                ),
                FieldSchema(
                    name = "query",
                    dtype = DataType.VARCHAR,
                    max_length = 2048
                )
            ]
            schema = CollectionSchema(
                fields = fields,
                description = ""
            )
            self.__milvus_client = Collection(
                name = collection_name,
                schema = schema
            )
            self.__milvus_client.create_index(
                field_name = "embedded_query",
                index_params = {
                    "index_type": "HNSW",
                    "metric_type": "COSINE",
                    "params": {
                        "M": 16,
                        "efConstruction": 200
                    }
                }
            )
            self.__milvus_client.load()
            print(f"""
                [INFO] [backend.vector_databases_tests.utils.milvus_client] Created collection '{collection_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.milvus_client] Failed to create collection '{collection_name}'
                \t{str(e)}
            """)
            raise

    def __bind_collection(
            self,
            collection_name: str
        ):
        if not utility.has_collection(collection_name):
            raise ValueError(f"""
                [ERROR] [backend.vector_databases_tests.utils.milvus_client] Collection '{collection_name}' does not exist
            """)
        self.__milvus_client = Collection(collection_name)
        self.__milvus_client.load()

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
            objects = [
                ids,
                embedded_queries,
                queries
            ]

            self.__bind_collection(collection_name)
            self.__milvus_client.insert(objects)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.milvus_client] Failed to push to collection '{collection_name}'
                \t{str(e)}
            """)

    def retrieve_query(
            self,
            collection_name: str,
            embedded_query,
            top_k: int = 50
        ):
        try:
            search_params = {
                "metric_type": "COSINE",
                "params": {
                    "ef": 64
                }
            }

            self.__bind_collection(collection_name)
            response = self.__milvus_client.search(
                data = [embedded_query],
                anns_field = "embedded_query",
                param = search_params,
                limit = top_k,
                output_fields = []
            )
            return response
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.milvus_client] Failed to retrieve from collection '{collection_name}'
                \t{str(e)}
            """)        