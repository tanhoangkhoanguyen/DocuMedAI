from logger import get_logger

import warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from pymilvus import connections, FieldSchema, CollectionSchema, DataType, Collection, utility
from typing import List

LOGGER = get_logger(
    name = "Milvus_tool",
    level = "INFO"
)
_MILVUS_DICT = {}
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

    def embed_query(
            self,
            query: str
        ):
        embedded_query = self.__embedding_model.embed_query(query)
        return embedded_query

    def list_collections(self) -> List[str]:
        try:
            collections = utility.list_collections()
            return collections
        except Exception as e:
            LOGGER.error(f"Failed to list collections\n\t{str(e)}")
            return []
    
    def collection_exists(
            self,
            collection_name: str,
        ):
        try:
            if utility.has_collection(collection_name):
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Failed to check '{collection_name}' existence\n\t{e}")
            return False
    
    def delete_collection(
            self,
            collection_name: str
        ):
        try:
            if not self.collection_exists(collection_name):
                LOGGER.info(f"Collection '{collection_name}' doesnt exist")
                return
            
            utility.drop_collection(collection_name)
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
            LOGGER.info(f"Created collection '{collection_name}'")
        except Exception as e:
            LOGGER.error(f"Failed to create collection '{collection_name}'\n\t{str(e)}")
            raise

    def bind_collection(
            self, 
            collection_name: str, 
            is_retrieval: bool = False
        ):
        if self.__current_collection != collection_name:
            if not utility.has_collection(collection_name):
                raise ValueError(f"Collection '{collection_name}' does not exist")
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
                        "M": 64,
                        "efConstruction": 200
                    }
                }
            )
        except Exception as e:
            LOGGER.error(f"Failed to create index for collection '{collection_name}'\n\t{str(e)}")
            raise

    def push_documents(
            self,
            collection_name: str,
            ids: List[str],
            queries: List[str],
            embedded_queries: List[List[float]]
        ):
        try:
            self.bind_collection(collection_name)
            objects = [
                ids,
                embedded_queries,
                queries
            ]
            self._milvus_client.insert(objects)
            self._milvus_client.flush()
        except Exception as e:
            LOGGER.error(f"Failed to push to collection '{collection_name}'\n\t{str(e)}")

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
            resp = self._milvus_client.search(
                data = [embedded_query],
                anns_field = "embedded_query",
                param = search_params,
                limit = top_k,
                output_fields = ["id", "query"]
            )
            return resp
        except Exception as e:
            LOGGER.error(f"Failed to retrieve from collection '{collection_name}'\n\t{str(e)}")
            return []


def get_milvus_client(
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        embedding_dimension: int = 384
    ):
    if not embedding_model in _MILVUS_DICT:
        _MILVUS_DICT[embedding_model] = MilvusClient(
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension
        )
    return _MILVUS_DICT[embedding_model]