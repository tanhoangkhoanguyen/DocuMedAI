import asyncio
import torch
import warnings
warnings.filterwarnings("ignore")

from services.chatbot.core.tools.helper import UserMessagePreprocesser, LawTypeIdentifier
from services.chatbot.core.tools.rerank import ReRanker
from services.chatbot.core.retriever.elastic import ElasticSearcher
from services.chatbot.core.retriever.qdrant import QdrantSearcher
from services.chatbot.core.retriever.tavily import TavilySearcher


class ContextRetriever:
    def __init__(self):
        self.__user_message_preprocessor = UserMessagePreprocesser()
        self._law_type_identifier = LawTypeIdentifier()
        self.__elastic_searcher = ElasticSearcher()
        self.__qdrant_searcher = QdrantSearcher()
        self.__tavily_searcher = TavilySearcher()
        self.__reranker = ReRanker()

    def __remove_similar_documents(self, documents):
        unique_docs = list(set(documents))
        result = []
        for doc in unique_docs:
            is_substring = False
            for other in unique_docs:
                if doc != other and doc in other:
                    is_substring = True
                    break
            if not is_substring:
                result.append(doc)
        return result
    
    async def get_context_for_user_message(self, user_message):
        preprocessed_user_messages = self.__user_message_preprocessor.rephrase_user_message(user_message)
        law_type_related = self._law_type_identifier.identify_law_type_from_user_message(user_message)
        qdrant_results, elastic_results, tavily_result = await asyncio.gather(
            *(self.__qdrant_searcher.retrieve_single_query(query=query, collection_name=law_type_related) 
              for query in preprocessed_user_messages),
            *(self.__elastic_searcher.retrieve_doc(query=query, law_type=law_type_related) 
              for query in preprocessed_user_messages),
            *(self.__tavily_searcher.search(user_message))
        )

        retrieved_docs = []
        for result in qdrant_results:
            for doc in result:
                retrieved_docs.append(doc.payload['text'])
        for result in elastic_results:
            for doc in result:
                retrieved_docs.append(doc['_source']['text'])
        
        unique_docs = self.__reranker.remove_similar_documents(retrieved_docs)
        reliable_docs, unreliable_docs = self.__reranker.rerank(user_message, unique_docs)
        return reliable_docs, unreliable_docs, tavily_result


        
        
        
    
