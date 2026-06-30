from langsmith import traceable
from langchain_core.messages import SystemMessage, HumanMessage
# Gemini is reached through the Go LLM proxy via its OpenAI-compatible endpoint
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from typing import List

import os, torch, warnings
warnings.filterwarnings("ignore")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

from services.chatbot.constants.prompts import (
    PARAPHRASE_MESSAGE_PROMPT,
    GENERALIZE_USER_MESSAGE_PROMPT,
)
from services.chatbot.tools.llm_config import get_llm_base_url  # route LLM calls via the Go proxy
from logger import get_logger


_LOGGER = get_logger(
    name = "rag_class",
    level = "INFO"
)
_RAG_DICT = {}


class RAG:
    def __init__(
        self,
        chat_model: str,
        temperature: float,
        reranking_model: str,
        reranking_threshold: float,
    ):
        self.__llm = ChatOpenAI(
            model = chat_model,
            temperature = temperature,
            base_url = get_llm_base_url(),  # → Go LLM proxy → Gemini (OpenAI-compat)
            api_key = GEMINI_API_KEY,
        )
        self.__reranking_threshold = reranking_threshold
        try:
            self.__tokenizer = AutoTokenizer.from_pretrained(reranking_model)
            self.__device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self.__model = AutoModelForSequenceClassification.from_pretrained(reranking_model).eval().to(self.__device)
        except Exception as e:
            _LOGGER.error(f"Failed to initialize reranker\n\t{str(e)}")
            raise

    class model_paraphrase_output_schema(BaseModel):
        messages: List[str]

    @traceable
    def paraphrase_message(self, message: str, number: int = 1):
        """
        If ``number`` is set, return multiple paraphrases for retrieval breadth.
        Otherwise return one generalized query string.
        """
        prompt = [
            SystemMessage(
                content = PARAPHRASE_MESSAGE_PROMPT.format(
                    number = number,
                    message = message,
                )
            ),
            HumanMessage(content = message),
        ]
        queries = self.__llm.with_structured_output(RAG.model_paraphrase_output_schema).invoke(prompt)
        return queries.messages

    @traceable
    def generalize_message(self, message:str):
        prompt = [
            SystemMessage(content = GENERALIZE_USER_MESSAGE_PROMPT),
            HumanMessage(content = message),
        ]
        query = self.__llm.invoke(prompt)
        return query.content

    def _move_to_device(self, batch):
        return {k: v.to(self.__device) for k, v in batch.items()}

    def score_documents(self, pairs: List[List[str]]) -> float:
        inputs = self.__tokenizer(
            pairs,
            padding = True,
            truncation = True,
            return_tensors = "pt",
            max_length = 512,
        )
        inputs = self._move_to_device(inputs)
        with torch.no_grad():
            logits = self.__model(**inputs).logits.squeeze(-1)
        return logits

    def same_topic(self, user_message: str, compressed_prior: str, threshold: float) -> bool:
        """
        Returns True if the new user message continues the same topic as compressed short-term memory.
        Empty prior always counts as same topic (nothing to compare).
        """
        if not compressed_prior.strip():
            return True
        logit = self.score_documents(pairs = [[user_message, compressed_prior]])
        score = float(logit[0].item())
        return score >= threshold

    @traceable
    def rerank_queries(self, queries: List[str], original_query: str, top_k: int) -> List[str]:
        if not queries:
            return []
        logits = self.score_documents(pairs = [[original_query, q] for q in queries])
        sorted_indices = torch.argsort(logits, descending = True)
        results: List[str] = []
        limit = min(top_k, len(sorted_indices))
        for i in range(limit):
            idx = int(sorted_indices[i].item())
            sc = float(logits[idx].item())
            if sc < self.__reranking_threshold:
                break
            results.append(queries[idx])
        return results


def get_rag_client(
        chat_model: str = "gemini-2.5-flash",
        temperature: float = 0,
        reranking_model: str = "BAAI/bge-reranker-v2-m3",
        reranking_threshold: float = -5,
    ):
    key = (
        chat_model,
        temperature,
        reranking_model,
        reranking_threshold,
    )
    if key not in _RAG_DICT:
        _RAG_DICT[key] = RAG(
            chat_model = chat_model,
            temperature = temperature,
            reranking_model = reranking_model,
            reranking_threshold = reranking_threshold,
        )
    return _RAG_DICT[key]