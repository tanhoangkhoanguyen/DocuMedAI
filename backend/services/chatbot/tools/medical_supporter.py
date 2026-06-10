from typing import List

from services.chatbot.constants.schemas import ToolParameter
from vector_database_tests.utils.qdrant_client import get_qdrant_client
from services.chatbot.tools.rag import get_rag_client


_COLLECTION_NAME = "MedicalTerms"
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


def _payload_texts_from_response(resp) -> List[str]:
    if not resp:
        return []
    points = getattr(resp, "points", None)
    if points is None and isinstance(resp, dict):
        points = resp.get("points")
    if not points:
        return []
    out: List[str] = []
    for p in points:
        payload = getattr(p, "payload", None) or {}
        if not isinstance(payload, dict):
            payload = {}
        text = payload.get("query") or payload.get("text") or ""
        if text:
            out.append(str(text))
    return out


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
        for msg in seeds:
            if not msg or not str(msg).strip():
                continue
            vec = self.__qdrant_client.embed_query(str(msg))
            resp = self.__qdrant_client.retrieve_query(
                collection_name = _COLLECTION_NAME,
                embedded_query = vec,
            )
            queries.extend(_payload_texts_from_response(resp))

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