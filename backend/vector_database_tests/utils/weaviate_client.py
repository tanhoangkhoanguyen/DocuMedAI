from logger import get_logger

import torch, weaviate, warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from typing import List

LOGGER = get_logger(
    name = "Weaviate_tool",
    level = "INFO"
)
_WEAVIATE_DICT = {}
WEAVIATE_URL = "http://la-weaviate:8080"

class WeaviateClient:
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
        self.__weaviate_client = weaviate.Client(url = WEAVIATE_URL)

    def embed_query(
            self,
            query: str
        ):
        embedded_query = self.__embedding_model.embed_query(query)
        return embedded_query

    def list_collections(self):
        try:
            schema = self.__weaviate_client.schema.get()
            classes = [cls['class'] for cls in schema.get("classes", [])]
            return classes
        except Exception as e:
            LOGGER.error(f"Failed to list classes\n\t{str(e)}")
            raise
    
    def collection_exists(
            self,
            class_name: str,
        ):
        try:
            if class_name in self.list_collections():
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Failed to check '{class_name}' existence\n\t{e}")
            return False

    def delete_collection(
            self,
            class_name: str
        ):
        try:
            if not self.collection_exists(class_name):
                LOGGER.info(f"Class '{class_name}' doesnt exist")
                return
            
            self.__weaviate_client.schema.delete_class(class_name)
            LOGGER.info(f"Deleted class '{class_name}'")
        except Exception as e:
            LOGGER.error(f"Failed to delete class '{class_name}'\n\t{str(e)}")
            raise

    def create_collection(
            self,
            class_name: str
        ):
        try:
            self.delete_collection(class_name)
            self.__weaviate_client.schema.create_class({
                "class": class_name,
                "vectorizer": "none", 
                "vectorIndexType": "hnsw",
                "vectorIndexConfig": {
                    "ef": 64,
                    "efConstruction": 200, 
                    "M": 64
                },
                "properties": [
                    {
                        "name": "query",
                        "dataType": ["string"]
                    }
                ]
            })
            LOGGER.info(f"Created class '{class_name}'")
        except Exception as e:
            LOGGER.error(f"Failed to create class '{class_name}'\n\t{str(e)}")
            raise

    def push_documents(
            self,
            class_name: str,
            ids: List[str],
            queries: List[str],
            embedded_queries: List[List[float]]
        ):
        try:
            with self.__weaviate_client.batch as batch:
                batch.batch_size = 1000
                for idx in range(len(ids)):
                    batch.add_data_object(
                        data_object = {"query": queries[idx]},
                        class_name = class_name,
                        vector = embedded_queries[idx],
                        uuid = ids[idx]
                    )
        except Exception as e:
            LOGGER.error(f"Failed to push to class '{class_name}'\n\t{str(e)}")

    def retrieve_query(
            self, 
            class_name: str,
            embedded_query: List[float],
            top_k: int = 50
        ):
        try:
            resp = self.__weaviate_client.query.get(class_name, ["*"]) \
                .with_near_vector({"vector": embedded_query}) \
                .with_limit(top_k) \
                .do()
            return resp
        except Exception as e:
            LOGGER.error(f"Failed to retrieve from class '{class_name}'\n\t{str(e)}")
            return []


def get_weaviate_client(
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        embedding_dimension: int = 384
    ):
    if not embedding_model in _WEAVIATE_DICT:
        _WEAVIATE_DICT[embedding_model] = WeaviateClient(
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension
        )
    return _WEAVIATE_DICT[embedding_model]