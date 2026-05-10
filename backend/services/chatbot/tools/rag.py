from langsmith import traceable
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
# from langchain_openai import ChatOpenAI
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
        try:
            self.__tokenizer = AutoTokenizer.from_pretrained(reranking_model)
            self.__device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self.__model = AutoModelForSequenceClassification.from_pretrained(reranking_model).eval().to(self.__device)
        except Exception as e:
            _LOGGER.error(f"Failed to initialize reranker\n\t{str(e)}")
        self.__reranking_threshold = reranking_threshold

    class model_paraphrase_output_schema(BaseModel):
        messages: List[str]

    @traceable
    def generalize_message(self, message: str, number: int):
        prompt = [
            SystemMessage(content = PARAPHRASE_MESSAGE_PROMPT.format(number = number)),
            HumanMessage(content = f"User's message: {message}")
        ]
        queries = self.__llm.with_structured_output(RAG.model_paraphrase_output_schema).invoke(prompt)
        return queries.messages

    @traceable
    def generalize_message(self, message:str):
        prompt = [
            SystemMessage(content = GENERALIZE_USER_MESSAGE_PROMPT),
            HumanMessage(content = message)
        ]
        query = self.__llm.invoke(prompt)
        return query.content

    def rerank_queries(self, queries: List[str], original_query: str, top_k: int):
        pairs = [[original_query, query] for query in queries]
        inputs = self.__tokenizer(pairs, padding = True, truncation = True, return_tensors = "pt").to(self.__device)
        inputs = inputs.to(self.__device)
        with torch.no_grad():
            scores = self.__model(**inputs).logits.squeeze(-1)
        sorted_indices = torch.argsort(scores, descending = True)
        bound = min(top_k, len(sorted_indices))
        while bound >= 0 and sorted_indices[bound] < self.__reranking_threshold:
            bound -= 1
        return sorted_indices[:bound]