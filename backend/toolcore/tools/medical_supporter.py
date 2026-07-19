from typing import List

from toolcore.contracts import RuntimeConfig
from vector_database_tests.utils.qdrant_client import get_qdrant_client
from utils.rag import get_rag_client


_MEDICAL_COLLECTION = "MedicalTerms"
_MEDICAL_SUPPORTER_DICT: dict = {}


def _config_key(config: RuntimeConfig) -> tuple:
    return (
        config.chat_model,
        config.temperature,
        config.embedding_model,
        config.embedding_dimension,
        config.reranking_model,
        config.reranking_threshold,
    )


class MedicalSupporter:
    def __init__(self, config: RuntimeConfig):
        self.__rag_client = get_rag_client(
            chat_model = config.chat_model,
            temperature = config.temperature,
            reranking_model = config.reranking_model,
            reranking_threshold = config.reranking_threshold,
        )
        self.__qdrant_client = get_qdrant_client(
            embedding_model = config.embedding_model,
            embedding_dimension = config.embedding_dimension,
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
                top_k = 3,  # 4 seeds * 5
                with_payload = True,
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


def get_medical_supporter(config: RuntimeConfig) -> MedicalSupporter:
    key = _config_key(config)
    if key not in _MEDICAL_SUPPORTER_DICT:
        _MEDICAL_SUPPORTER_DICT[key] = MedicalSupporter(config)
    return _MEDICAL_SUPPORTER_DICT[key]