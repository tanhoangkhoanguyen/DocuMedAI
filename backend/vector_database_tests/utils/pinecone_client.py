# collection_name = "latencytest"
from logger import get_logger

import torch, requests, asyncio, aiohttp, warnings
warnings.filterwarnings("ignore")
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from langchain_community.embeddings import HuggingFaceEmbeddings
from typing import List

LOGGER = get_logger(__name__)
PINECONE_CONTROL_URL = "http://la-pinecone:5080"
PINECONE_DATA_URL = "http://la-pinecone:5081"
PINECONE_API_KEY = "pclocal"

class PineconeClient:
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
            "Api-Key": PINECONE_API_KEY,
            "Connection": "close" # "keep-alive"
        })
        adapter = HTTPAdapter(
            pool_connections = 32, 
            pool_maxsize = 64, 
            # max_retries = Retry(
            #     total = 5,
            #     backoff_factor = 0.1,
            #     status_forcelist = [500, 502, 503, 504]
            # )
        )
        self.__session.mount("http://", adapter)
        self.__session.mount("https://", adapter)
        self.__index_hosts = {}
    
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

    def list_collections(self):
        try:
            resp = self.__session.get(f"{PINECONE_CONTROL_URL}/indexes")
            self.__debug_session(resp)
            collections_info = resp.json()
            collection_names = [idx["name"] for idx in collections_info.get("indexes", [])]
            return collection_names
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
 
            resp = self.__session.delete(f"{PINECONE_CONTROL_URL}/indexes/{collection_name}")
            self.__debug_session(resp)
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
            resp = self.__session.post(
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
            self.__debug_session(resp)
            LOGGER.info(f"Created collection '{collection_name}'")
        except Exception as e:
            LOGGER.info(f"Failed to create collection '{collection_name}'\n\t{str(e)}")
            raise

    def __get_host(self, collection_name: str) -> str:
        if collection_name not in self.__index_hosts:
            r = self.__session.get(f"{PINECONE_CONTROL_URL}/indexes/{collection_name}")
            host = r.json()["host"].replace("localhost", "la-pinecone")
            self.__index_hosts[collection_name] = host
        return self.__index_hosts[collection_name]

    async def __push_document(
            self,
            session,
            collection_name: str,
            ids: str,
            queries: str, 
            embedded_queries: List[float]
        ):
        try:
            docs = [{
                    "id": ids[idx],
                    "values": embedded_queries[idx],
                    "metadata": {"query": queries[idx]}
                } for idx in range(len(ids))
            ]
            host = self.__get_host(collection_name)
            url = f"http://{host}/vectors/upsert"
            async with session.post(url, json = {"vectors": docs}) as resp:
                if not resp.ok:
                    text = await resp.text()
                    LOGGER.error(text)
                    resp.raise_for_status()
        except Exception as e:
            LOGGER.error(f"Failed to push document batch\n\t{str(e)}")

    async def __push_documents(
            self,
            collection_name: str,
            ids: List[str],
            queries: List[str],
            embedded_queries: List[List[float]],
            batch_size: int = 100
        ):
        try:
            if not (len(ids) == len(queries) == len(embedded_queries)):
                raise ValueError("ids, queries, vectors length mismatch")
            async with aiohttp.ClientSession() as session:
                tasks = []
                for i in range(0, len(ids), batch_size):
                    batch_ids = ids[i:min(i + batch_size, len(ids))]
                    batch_queries = queries[i:min(i + batch_size, len(ids))]
                    batch_vectors = embedded_queries[i:min(i + batch_size, len(ids))]

                    tasks.append(
                        self.__push_document(
                            session, 
                            collection_name, 
                            batch_ids, 
                            batch_queries, 
                            batch_vectors
                        )
                    )
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
            embedded_query,
            top_k: int = 50
        ):
        try:
            host = self.__get_host(collection_name)
            resp = self.__session.post(
                f"http://{host}/query",
                json = {
                    'vector': embedded_query,
                    'topK': top_k,
                    'includeMetadata': True
                }
            )
            self.__debug_session(resp)
            return resp.json()
        except Exception as e:
            LOGGER.error(f"Failed to retrieve from collection '{collection_name}'\n\t{str(e)}")
            return []