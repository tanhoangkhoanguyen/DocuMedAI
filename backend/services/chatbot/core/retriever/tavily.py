import os
import requests
from dotenv import load_dotenv
load_dotenv()
from langsmith import traceable


class TavilySearcher:
    def __init__(self):
        self.__search_url = os.getenv("TAVILY_SEARCH_URL")
        self.__tavily_api_key = os.getenv("TAVILY_API_KEY")
        self.__headers = {
            "Authorization": f"Bearer {self.__tavily_api_key}",
            "Content-Type": "application/json"
        }
        self.__payload = {
            "query": None,
            "search_depth": "advanced",
            "include_answer": False,
            "max_results": None
        }
    
    def __set_paylooad(self, query: str, max_results: int):
        if self.__payload["query"] is not None:
            print(f'[WARNING] From TavilySearcher: Override existent query {self.__payload["query"]} to {query}')
        self.__payload["query"] = query
        
        if self.__payload["max_results"] is not None:
            print(f'[WARNING] From TavilySearcher: Override existent max_results {self.__payload["max_results"]} to {max_results}')
        self.__payload["max_results"] = max_results
    
    @traceable
    def search(self, query: str, max_results:int=3):
        result = {}
        self.__set_paylooad(query=query, max_results=max_results)
        try:
            response = requests.post(
                url=self.__search_url,
                json=self.__payload,
                headers=self.__headers
            )
            result = response.json()
        except Exception as e:
            print(f"[ERROR] From TavilySearcher: {str(e)}")
        finally:
            self.__payload["query"] = None
            self.__payload["max_results"] = None
            print(f'[INFO] From TavilySearcher: Done search')

        return result
