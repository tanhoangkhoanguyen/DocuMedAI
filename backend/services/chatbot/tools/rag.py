from langsmith import traceable
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from typing import List

import torch, warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()

from services.chatbot.constants.prompts import (
    PARAPHRASE_MESSAGE_PROMPT,
    GENERALIZE_USER_MESSAGE_PROMPT,
)
from logger import get_logger


_LOGGER = get_logger(
    name = "rag_class",
    level = "INFO"
)


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

    def same_topic(self, user_message: str, compressed_prior: str) -> bool:
        """
        Returns True if the new user message continues the same topic as compressed short-term memory.
        Empty prior always counts as same topic (nothing to compare).
        """
        if not compressed_prior.strip():
            return True
        score = self.score_query_document(user_message, compressed_prior)
        return score >= self.__reranking_threshold

    def score_query_document(self, query: str, document: str) -> float:
        pairs = [[query, document]]
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
        return float(logits[0].item())

    @traceable
    def rerank_queries(self, queries: List[str], original_query: str, top_k: int) -> List[str]:
        if not queries:
            return []
        pairs = [[original_query, q] for q in queries]
        inputs = self.__tokenizer(
            pairs,
            padding = True,
            truncation = True,
            return_tensors = "pt",
            max_length = 512,
        )
        inputs = self._move_to_device(inputs)
        with torch.no_grad():
            scores = self.__model(**inputs).logits.squeeze(-1)
        sorted_indices = torch.argsort(scores, descending = True)
        results: List[str] = []
        limit = min(top_k, len(sorted_indices))
        for i in range(limit):
            idx = int(sorted_indices[i].item())
            sc = float(scores[idx].item())
            if sc < self.__reranking_threshold:
                break
            results.append(queries[idx])
        return results