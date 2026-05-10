from dotenv import load_dotenv
load_dotenv()

import os, requests
from langsmith import traceable


class TavilySearcher:
    def __init__(self):
        self.__headers = {
            "Authorization": f'Bearer {os.getenv("TAVILY_API_KEY")}',
            "Content-Type": "application/json"
        }
    
    @traceable
    def search_query(self, query: str, max_results:int = 3):
        payload = {
            "query": query,
            "search_depth": "advanced",
            "include_answer": False,
            "max_results": max_results
        }
        response = requests.post(
            url = os.getenv("TAVILY_SEARCH_URL"),
            json = payload,
            headers = self.__headers
        )
        return response.json()