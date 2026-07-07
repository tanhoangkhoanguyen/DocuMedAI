"""
MCP tool backing: retrieve passages from the CURRENT USER's uploaded documents.

Mirrors medical_supporter.py but queries the UserDocuments collection with a
mandatory user_id filter, so one user can never retrieve another user's chunks.
Query flow: paraphrase/generalize -> embed each -> filtered top-k retrieve ->
rerank -> return the best passages for the answering agent to ground on.
"""
from typing import List

from services.chatbot.constants.schemas import ToolParameter
from services.documents_upload.constants import USER_DOCUMENTS_COLLECTION
from vector_database_tests.utils.qdrant_client import get_qdrant_client
from services.chatbot.tools.rag import get_rag_client


_USER_DOCUMENT_SUPPORTER_DICT: dict = {}


def _payload_key(payload: ToolParameter) -> tuple:
    return (
        payload.chat_model,
        payload.temperature,
        payload.embedding_model,
        payload.embedding_dimension,
        payload.reranking_model,
        payload.reranking_threshold,
    )


class UserDocumentSupporter:
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

    def run(self, message: str, user_id: str, config = None) -> str:
        # No user id => no isolation guarantee => retrieve nothing.
        if not user_id:
            return ""

        paraphrased_msgs = self.__rag_client.paraphrase_message(message, 3)
        generalized_msg = self.__rag_client.generalize_message(message)
        seeds = list(paraphrased_msgs) + [generalized_msg]

        passages: List[str] = []
        seen = set()
        for msg in seeds:
            if not msg or not str(msg).strip():
                continue
            embedded_query = self.__qdrant_client.embed_query(str(msg))
            resp = self.__qdrant_client.retrieve_query(
                collection_name = USER_DOCUMENTS_COLLECTION,
                embedded_query = embedded_query,
                top_k = 3,  # 4 seeds * 5
                user_id = user_id,               # per-user isolation
                with_payload = True,             # need the chunk text
            )
            for text in self.__qdrant_client._payload_texts_from_response(resp):
                if text not in seen:
                    seen.add(text)
                    passages.append(text)

        if not passages:
            return ""

        ranked = self.__rag_client.rerank_queries(
            queries = passages,
            original_query = message,
            top_k = 1,
        )
        if ranked:
            return ranked[0]
        return ""


def get_user_document_supporter(payload: ToolParameter) -> "UserDocumentSupporter":
    key = _payload_key(payload)
    if key not in _USER_DOCUMENT_SUPPORTER_DICT:
        _USER_DOCUMENT_SUPPORTER_DICT[key] = UserDocumentSupporter(payload)
    return _USER_DOCUMENT_SUPPORTER_DICT[key]
