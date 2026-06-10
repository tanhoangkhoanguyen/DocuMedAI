from logger import get_logger

import torch, warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from elasticsearch import Elasticsearch
from typing import List

LOGGER = get_logger(
    name = "ElasticSearch_tool",
    level = "INFO"
)
_ELASTICSEARCH_DICT = {}
ELASTICSEARCH_URL = "http://la-elasticsearch:9200"

class ElasticsearchClient:
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
        self.__client = Elasticsearch(
            ELASTICSEARCH_URL,
            verify_certs = False
        )

    def list_collections(self) -> List[str]:
        try:
            indices = self.__client.indices.get_alias().keys()
            return list(indices)
        except Exception as e:
            LOGGER.error(f"Failed to list indices\n\t{str(e)}")
            return []

    def collection_exists(self, collection_name: str) -> bool:
        try:
            return collection_name in self.list_collections()
        except Exception as e:
            LOGGER.error(f"Failed to check '{collection_name}' existence\n\t{str(e)}")
            return False

    def delete_collection(self, collection_name: str):
        try:
            if not self.collection_exists(collection_name):
                LOGGER.info(f"Index '{collection_name}' doesn't exist")
                return

            self.__client.indices.delete(index=collection_name)
            LOGGER.info(f"Deleted index '{collection_name}'")
        except Exception as e:
            LOGGER.error(f"Failed to delete index '{collection_name}'\n\t{str(e)}")
            raise

    def create_collection(self, collection_name: str):
        mappings = {
            "properties": {
                "query": {
                    "type": "text",
                    "analyzer": "english"
                }
            }
        }
        try:
            self.delete_collection(collection_name)
            self.__client.indices.create(
                index=collection_name,
                mappings=mappings
            )
            LOGGER.info(f"Created index '{collection_name}'")
        except Exception as e:
            LOGGER.error(f"Failed to create index '{collection_name}'\n\t{str(e)}")
            raise

    def push_documents(
            self,
            collection_name: str,
            ids: List[str],
            queries: List[str],
            embedded_queries = None
        ):
        try:
            actions = []
            for idx in range(len(ids)):
                actions.append({
                    "_index": collection_name,
                    "_id": ids[idx],
                    "_source": {"query": queries[idx]}
                })

            for action in actions:
                self.__client.index(
                    index=action["_index"],
                    id=action["_id"],
                    document=action["_source"]
                )
        except Exception as e:
            LOGGER.error(f"Failed to push to index '{collection_name}'\n\t{str(e)}")

    def retrieve_query(
            self,
            collection_name: str,
            query: str,
            top_k: int = 50
        ):
        try:
            response = self.__client.search(
                index=collection_name,
                size=top_k,
                query={"match": {"query": query}}
            )
            return response["hits"]["hits"]
        except Exception as e:
            LOGGER.error(f"Failed to retrieve from index '{collection_name}'\n\t{str(e)}")
            return []

    def close(self):
        self.__client.close()

def get_elasticsearch_client(
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        embedding_dimension: int = 384
    ):
    if not embedding_model in _ELASTICSEARCH_DICT:
        _ELASTICSEARCH_DICT[embedding_model] = ElasticsearchClient(
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension
        )
    return _ELASTICSEARCH_DICT[embedding_model]