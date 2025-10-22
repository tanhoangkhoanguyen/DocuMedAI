import asyncio
import os
import warnings
warnings.filterwarnings("ignore")
from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED

from services.chatbot.core.tools.helper import UserMessagePreprocesser, LawTypeIdentifier
from services.chatbot.core.tools.rerank import ReRanker
from services.chatbot.core.retriever.elastic import ElasticSearcher
from services.chatbot.core.retriever.qdrant import QdrantSearcher
from services.chatbot.core.retriever.tavily import TavilySearcher


class ContextRetriever:
    def __init__(
            self, 
            chat_model:str, 
            embedding_model:str,
            reranking_model:str,
            reranking_threshold:str,
            max_workers:int, 
            timeout = None
        ):
        self.__max_workers = max_workers
        self.__user_message_preprocessor = UserMessagePreprocesser(
            chat_model = chat_model, 
            max_workers = max_workers
        )
        self.__law_type_identifier = LawTypeIdentifier(chat_model = chat_model)
        self.__qdrant_searcher = QdrantSearcher(embedding_model = embedding_model)
        self.__elastic_searcher = ElasticSearcher()
        self.__tavily_searcher = TavilySearcher()
        self.__reranker = ReRanker(
            reranking_model = reranking_model,
            reranking_threshold = reranking_threshold
        )
    
    def get_context_for_user_message(self, user_message, timeout = None):
        preprocessed_user_messages = self.__user_message_preprocessor.rephrase_user_message(user_message = user_message)
        law_type = self.__law_type_identifier.identify_law_type(user_message = user_message)
        
        qdrant_results = []
        elastic_results = []
        tavily_result = []
        futures = {} 

        with ThreadPoolExecutor(max_workers = self.__max_workers) as pool:
            for message in preprocessed_user_messages:
                f1 = pool.submit(self.__qdrant_searcher.retrieve_query, message, law_type)
                futures[f1] = ("qdrant", message)
                f2 = pool.submit(self.__elastic_searcher.retrieve_query, message, law_type)
                futures[f2] = ("elastic", message)

            f3 = pool.submit(self.__tavily_searcher.search_query, user_message)
            futures[f3] = ("tavily", user_message)

            done, not_done = wait(futures.keys(), timeout = timeout, return_when = ALL_COMPLETED)

            for f in not_done:
                kind, query = futures[f]
                print(f'[ERROR] From ContextRetriever: Multi-threading for {kind} search is not done. Detailed: Due to timeout {timeout}')
                f.cancel()

            for f in done:
                kind, query = futures[f]
                try:
                    res = f.result() 
                except Exception as e:
                    print(f"[ERROR] ContextRetriever: task failed for {kind} ({query}): {e}")
                    continue
                if kind == "qdrant":
                    qdrant_results.append(res)
                elif kind == "elastic":
                    elastic_results.append(res)
                elif kind == "tavily":
                    tavily_result = res

        if not_done:
            print(f"[WARN] {len(not_done)} task(s) did not complete before timeout")

        retrieved_docs = []
        for batch in qdrant_results:
            for point in batch:
                try:
                    retrieved_docs.append(point.payload["text"])
                except Exception:
                    continue
        for batch in elastic_results:
            for hit in batch:
                try:
                    retrieved_docs.append(hit["_source"]["text"])
                except Exception:
                    continue

        unique_docs = self.__reranker.remove_similar_documents(retrieved_docs)
        reliable_docs, unreliable_docs = self.__reranker.rerank(user_message, unique_docs)
        return reliable_docs, unreliable_docs, tavily_result