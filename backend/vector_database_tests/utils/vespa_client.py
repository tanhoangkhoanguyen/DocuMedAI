# docker restart la-vespa-service
# docker exec -it la-vespa-service curl -s http://localhost:19071/state/v1/health
# docker exec -it la-vespa-service curl -s http://localhost:8080/state/v1/health
# docker exec -it la-vespa-service vespa deploy /app
# docker exec -it la-backend-service python -m vector_database_tests.data_uploading
from logger import get_logger

import torch, requests, asyncio, aiohttp, warnings
warnings.filterwarnings("ignore")
from requests.adapters import HTTPAdapter
from langchain_community.embeddings import HuggingFaceEmbeddings
from typing import List

LOGGER = get_logger(
    name = "Vespa_tool",
    level = "INFO"
)
VESPA_URL = "http://la-vespa:8080"

class VespaClient:
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
        self.__session = requests.Session()
        self.__session.headers.update({
            "Connection": "keep-alive"
        })
        adapter = HTTPAdapter(pool_connections = 32, pool_maxsize = 64)
        self.__session.mount("http://", adapter)
        self.__session.mount("https://", adapter)
    
    def embed_query(
            self,
            query: str
        ):
        embedded_query = self.__embedding_model.embed_query(query)
        return embedded_query

    def __debug_session(self, resp):
        if not resp.ok:
            LOGGER.error(f"response body: {resp.text}")
        resp.raise_for_status()

    def list_collections(self) -> List[str]:
        try:
            def _walk(node):
                if isinstance(node, dict):
                    v = node.get("value")
                    if isinstance(v, str):
                        yield v
                    for child in node.get("children", []):
                        yield from _walk(child)
                elif isinstance(node, list):
                    for item in node:
                        yield from _walk(item)

            body = {
                "yql": (
                    "select collection from vector where true "
                    "| all(group(collection) each(output(count())))"
                ),
                "hits": 0,
            }
            resp = self.__session.post(f"{VESPA_URL}/search/", json = body)
            self.__debug_session(resp)
            hits = list(_walk(resp.json().get("root", {})))
            collections = [hit for hit in hits if hit != "__dummy__"]
            return collections
        except Exception as e:
            LOGGER.error(f"Failed to list collections\n\t{e}")
            return []
    
    def collection_exists(
            self,
            collection_name: str,
        ):
        try:
            if collection_name in self.list_collections():
                return True
            return False
        except Exception as e:
            LOGGER.error(f"Failed to check '{collection_name}' existence\n\t{e}")
            return False

    def delete_collection(
            self,
            collection_name: str,
        ):
        try:
            if not self.collection_exists(collection_name):
                LOGGER.info(f"Collection '{collection_name}' does not exist")
                return
 
            resp = self.__session.delete(
                f"{VESPA_URL}/document/v1/mynamespace/vector/docid/",
                params = {
                    "selection": f'vector.collection == "{collection_name}"', 
                    "cluster": "vector"
                }
            )
            self.__debug_session(resp)
            LOGGER.info(f"Deleted collection '{collection_name}'")
        except Exception as e:
            LOGGER.error(f"Failed to delete collection '{collection_name}'\n\t{str(e)}")
            raise

    def create_collection(self, collection_name: str):
        try:
            self.delete_collection(collection_name)
            doc = {
                "fields": {
                    "collection": collection_name,
                    "query": "__dummy__",
                    "embedded_query": [0.0] * self.__embedding_dimension
                }
            }
            url = f"{VESPA_URL}/document/v1/mynamespace/vector/docid/{collection_name}-dummy"
            resp = self.__session.post(url, json = doc)
            self.__debug_session(resp)
            LOGGER.info(f"Created collection '{collection_name}'")
        except Exception as e:
            LOGGER.info(f"Failed to create collection '{collection_name}'\n\t{str(e)}")
            raise

    async def __push_document(
            self,
            session,
            collection_name: str,
            id: str,
            query: str, 
            embedded_query: List[float]
        ):
        try:
            doc = {
                "fields": {
                    "collection": collection_name,
                    "query": query,
                    "embedded_query": embedded_query
                }
            }
            url = f"{VESPA_URL}/document/v1/mynamespace/vector/docid/{id}"
            async with session.post(url, json = doc) as resp:
                if not resp.ok:
                    text = await resp.text()
                    LOGGER.error(text)
                    resp.raise_for_status()
        except Exception as e:
            LOGGER.error(f"Failed to push document\n\t{str(e)}")

    async def __push_documents(
            self,
            collection_name: str,
            ids: List[str],
            queries: List[str],
            embedded_queries: List[List[float]]
        ):
        try:
            if not (len(ids) == len(queries) == len(embedded_queries)):
                raise ValueError("ids, queries, vectors length mismatch")
            async with aiohttp.ClientSession() as session:
                tasks = [
                    self.__push_document(
                        session,
                        collection_name, 
                        ids[i], 
                        queries[i], 
                        embedded_queries[i]
                    )
                    for i in range(len(ids))
                ]
                await asyncio.gather(*tasks)
        except Exception as e:
            LOGGER.error(f"Failed to push documents\n\t{str(e)}")
        
    def push_documents(
            self,
            collection_name: str,
            ids: List[str],
            queries: List[str],
            embedded_queries: List[List[float]]
        ):
        asyncio.run(self.__push_documents(
            collection_name,
            ids,
            queries, 
            embedded_queries
        ))

    def retrieve_query(
            self,
            collection_name: str,
            embedded_query: List[float],
            top_k: int = 50
        ):
        try:
            body = {
                "yql": (
                    f"select query from vector where "
                    f"{{targetHits:5000}}nearestNeighbor(embedded_query,query_embedding) "
                    f"and collection contains '{collection_name}'"
                ),
                "input.query(query_embedding)": embedded_query,
                "ranking.profile": "default",
                "hits": top_k
            }
            resp = self.__session.post(
                    f"{VESPA_URL}/search/",
                    json = body
                )
            self.__debug_session(resp)
            return resp.json()
        except Exception as e:
            LOGGER.error(f"Failed to retrieve from collection '{collection_name}'")
            return []