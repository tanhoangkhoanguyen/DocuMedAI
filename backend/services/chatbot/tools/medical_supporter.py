from typing import List

from services.chatbot.constants.schemas import ToolParameter
from vector_database_tests.utils.qdrant_client import get_qdrant_client
from services.chatbot.tools.rag import get_rag_client


_MEDICAL_COLLECTION = "MedicalTerms"
_MEDICAL_SUPPORTER_DICT: dict = {}


def _payload_key(payload: ToolParameter) -> tuple:
    return (
        payload.chat_model,
        payload.temperature,
        payload.embedding_model,
        payload.embedding_dimension,
        payload.reranking_model,
        payload.reranking_threshold,
    )


class MedicalSupporter:
    def __init__(self, payload: ToolParameter):
        self.__rag_client = get_rag_client(
            chat_model = payload.chat_model,
            temperature = payload.temperature,
            reranking_model = payload.reranking_model,
            reranking_threshold = payload.reranking_threshold,
        )
        self.__qdrant_client = get_qdrant_client(
            embedding_model = payload.embedding_model,
            embedding_dimension = payload.embedding_dimension,
        )

    def run(self, message: str, config = None):
        paraphrased_msgs = self.__rag_client.paraphrase_message(message, 3)
        generalized_msg = self.__rag_client.generalize_message(message)
        seeds = list(paraphrased_msgs) + [generalized_msg]

        queries: List[str] = []
        seen = set()
        for msg in seeds:
            if not msg or not str(msg).strip():
                continue
            vec = self.__qdrant_client.embed_query(str(msg))
            resp = self.__qdrant_client.retrieve_query(
                collection_name = _MEDICAL_COLLECTION,
                embedded_query = vec,
            )
            for text in self.__qdrant_client._payload_texts_from_response(resp):
                if text not in seen:
                    seen.add(text)
                    queries.append(text)

        if not queries:
            return ""

        ranked = self.__rag_client.rerank_queries(
            queries = queries,
            original_query = message,
            top_k = 1,
        )
        if ranked:
            return ranked[0]
        return ""


def get_medical_supporter(payload: ToolParameter) -> MedicalSupporter:
    key = _payload_key(payload)
    if key not in _MEDICAL_SUPPORTER_DICT:
        _MEDICAL_SUPPORTER_DICT[key] = MedicalSupporter(payload)
    return _MEDICAL_SUPPORTER_DICT[key]