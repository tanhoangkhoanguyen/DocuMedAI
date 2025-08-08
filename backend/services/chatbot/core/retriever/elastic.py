import os, warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()

from elasticsearch import Elasticsearch

class ElasticSearcher:
    def __init__(self):
        try:
            self.__elastic_host = os.getenv("ELASTIC_HOST")
            self.__elastic_password = os.getenv("ELASTIC_PASSWORD")
        except Exception as e:
            print(f"[ERROR] From ElasticSearcher init: {str(e)}")
            print(f"Terminated")
            exit(1)

        self.__client = Elasticsearch(
            [self.__elastic_host],
            basic_auth = ("elastic", self.__elastic_password),
            verify_certs = False,
            ssl_show_warn = False
        )
        
        self.__search_body = {
            "query": {
                "match": {
                    "text": {
                        "query": None,
                        "fuzziness": "AUTO"
                    }
                }
            },
            "size": None
        }

    def __set_search_body(self, query, size = 3):
        if self.__search_body["query"]["match"]["text"]["query"] is not None:
            print(f"[WARNING] From ElasticSearcher: Override existent query {self.__search_body["query"]["match"]["text"]["query"]} to {query}")
        self.__search_body["query"]["match"]["text"]["query"] = query
        
        if self.__search_body["size"] is not None:
            print(f"[WARNING] From TavilySearcher: Override existent max_results {self.__search_body["size"]} to {size}")
        self.__search_body["size"] = size

    def retrieve_doc(self, query, law_type):
        self.__set_search_body(query = query)
        try:
            search_response = self.client.search(
                index = law_type,
                body = self.__search_body
            )
            return search_response['hits']['hits']
        except Exception as e:
            print(f"[ERROR] From ElasticSearcher: {str(e)}")
            return []
        finally:
            self.__search_body["query"]["match"]["text"]["query"] = None
            self.__search_body["size"] = None

    
