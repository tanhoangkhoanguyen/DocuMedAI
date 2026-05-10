from backend.services.chatbot.main import embedding_dimension, qdrant_client
from services.chatbot.tools.rag import RAG
from vector_database_tests.utils.qdrant_client import QdrantClient


_COLLECTION_NAME = "MedicalTerms"


class MedicalSupporter:
    def __init__(
            self, 
            chat_model: str, 
            temperature: float,
            embedding_model: str,
            embedding_dimension: int,
            reranking_model: str,
            reranking_threshold: float,
        ):
        self.__rag_client = RAG(
            chat_model = chat_model,
            temperature = temperature,
            reranking_model = reranking_model,
            reranking_threshold = reranking_threshold
        )
        self.qdrant_client = QdrantClient(
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension
        )

    def run(self, message: str, config = None):
        paraphrased_msgs = self.__rag_client.generalize_message(message = message, number = 3)
        generalized_msg = self.__rag_client.generalize_message(message = message)
        messages = paraphrased_msgs + [generalized_msg]

        queries = [
            query
            for msg in messages
            for query in qdrant_client.retrieve_query(
                collection_name = _COLLECTION_NAME,
                query = msg
            )
        ]
        result = self.__rag_client.rerank_queries(
            queries = queries,
            original_query = message,
            top_k = 1
        )
        return result[0]