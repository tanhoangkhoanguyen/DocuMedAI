import os, warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()

from elasticsearch import Elasticsearch

class ElasticSearcher:
    def __init__(self):
        self.__client = Elasticsearch(
            [os.getenv("ELASTIC_HOST")],
            basic_auth = ("elastic", os.getenv("ELASTIC_PASSWORD")),
            verify_certs = False,
            ssl_show_warn = False,
            request_timeout = None,
        )
        self.__search_body = {
                "match": {
                    "text": {
                        "query": None,
                        "fuzziness": "AUTO",
                    }
                }
            }
        self.__size = None

    def retrieve_query(self, query: str, law_type: str, size:int = 3):
        search_body = {
            "match": {
                "text": {
                    "query": query,
                    "fuzziness": "AUTO",
                }
            }
        }
        search_response = self.__client.search(
            index = law_type,
            query = search_body,
            size = size,
        )
        result = search_response.get("hits", {}).get("hits", [])
        return result