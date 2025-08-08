import os, warnings, asyncio, logging
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
from elasticsearch import Elasticsearch

load_dotenv()
logger = logging.getLogger(__name__)

class ElasticSearcher:
    def __init__(self):
        try:
            elastic_host = os.getenv("ELASTIC_HOST")
            elastic_password = os.getenv("ELASTIC_PASSWORD")
            if not elastic_host or not elastic_password:
                raise ValueError("ELASTIC_HOST or ELASTIC_PASSWORD is not set")
        except Exception as e:
            print(f"[ERROR] From ElasticSearcher init: {e}")
            print("Terminated")
            raise

        self.__client = Elasticsearch(
            [elastic_host],
            basic_auth=("elastic", elastic_password),
            verify_certs=False,
            ssl_show_warn=False,
            request_timeout=30,
        )

        self.__search_body = {
            "query": {
                "match": {
                    "text": {
                        "query": None,
                        "fuzziness": "AUTO",
                    }
                }
            }
        }
        self.__size = None

    def __set_search_body(self, query: str, size: int = 3):
        if self.__search_body["query"]["match"]["text"]["query"] is not None:
            old = self.__search_body["query"]["match"]["text"]["query"]
            print(f"[WARNING] From ElasticSearcher: Override existing query {old} to {query}")
        self.__search_body["query"]["match"]["text"]["query"] = query

        if self.__size is not None:
            print(f"[WARNING] From ElasticSearcher: Override existing size {self.__size} to {size}")
        self.__size = size

    def __helper_retrieve_doc(self, query: str, law_type: str):
        self.__set_search_body(query=query)
        try:
            es_query = self.__search_body["query"]
            search_response = self.__client.search(
                index=law_type,
                query=es_query,
                size=self.__size,
            )
            return search_response.get("hits", {}).get("hits", [])
        except Exception as e:
            print(f"[ERROR] From ElasticSearcher: {e}")
            return []
        finally:
            self.__search_body["query"]["match"]["text"]["query"] = None
            self.__size = None

    async def retrieve_doc(self, query: str, law_type: str):
        return await asyncio.to_thread(self.__helper_retrieve_doc, query, law_type)
