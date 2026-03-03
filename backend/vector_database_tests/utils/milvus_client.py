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
        self._milvus_client = None
        self.__current_collection = None
        self.__is_loaded = False

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
            if utility.has_collection(collection_name):
                utility.drop_collection(collection_name)
                print(f"""
                    [INFO] [backend.vector_databases_tests.utils.milvus_client] Deleted collection '{collection_name}'
                """)
            else:
                print(f"""
                    [INFO] [backend.vector_databases_tests.utils.milvus_client] Collection '{collection_name}' doesnt exist
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
                    max_length = 4096
                )
            ]
            schema = CollectionSchema(
                fields = fields,
                description = ""
            )
            self._milvus_client = Collection(
                name = collection_name,
                schema = schema
            )
            print(f"""
                [INFO] [backend.vector_databases_tests.utils.milvus_client] Created collection '{collection_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.milvus_client] Failed to create collection '{collection_name}'
                \t{str(e)}
            """)
            raise

    def embed_query(
            self,
            query: str
        ):
        embedded_query = self.__embedding_model.embed_query(query)
        return embedded_query

    def bind_collection(
            self, 
            collection_name: str, 
            is_retrieval: bool = False
        ):
        if self.__current_collection != collection_name:
            if not utility.has_collection(collection_name):
                raise ValueError(f"""
                    [ERROR] [backend.vector_databases_tests.utils.milvus_client] Collection '{collection_name}' does not exist
                """)
            self._milvus_client = Collection(collection_name)
            self.__current_collection = collection_name
            self.__is_loaded = False
        
        if is_retrieval and not self.__is_loaded:
            self._milvus_client.load()
            self.__is_loaded = True

    def create_index(self, collection_name: str):
        try:
            self.bind_collection(collection_name)
            self._milvus_client.create_index(
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
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.milvus_client] Failed to create index for collection '{collection_name}'
                \t{str(e)}
            """)
            raise

    def count_collection(
            self,
            collection_name: str
        ) -> int:
        self.bind_collection(collection_name)
        total_data = self._milvus_client.num_entities
        return total_data

    def push_to_collection(
            self,
            collection_name: str,
            ids: List[str],
            queries,
            embedded_queries
        ):
        try:
            self.bind_collection(collection_name)
            objects = [
                ids,
                embedded_queries,
                queries
            ]
            self._milvus_client.insert(objects)
            # self._milvus_client.flush()
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
            self.bind_collection(collection_name, True)
            search_params = {
                "metric_type": "COSINE",
                "params": {
                    "ef": 64
                }
            }
            response = self._milvus_client.search(
                data = [embedded_query],
                anns_field = "embedded_query",
                param = search_params,
                limit = top_k,
                output_fields = ["id", "query"]
            )
            return response
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.milvus_client] Failed to retrieve from collection '{collection_name}'
                \t{str(e)}
            """)
            return []