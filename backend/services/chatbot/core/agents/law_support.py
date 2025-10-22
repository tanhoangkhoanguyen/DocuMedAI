from services.chatbot.core.tools.retrieve_context import ContextRetriever
from services.chatbot.core.constants.prompts import LAW_GENERATION_PROMPT

from dotenv import load_dotenv
load_dotenv()

import time
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

class lawSupporter:
    def __init__(
            self, 
            chat_model:str, 
            embedding_model:str,
            reranking_model:str,
            reranking_threshold:str,
            max_workers:int,
            temperature:int = 0
        ):
        self.__llm = ChatOpenAI(model_name = chat_model, temperature = temperature)
        self.__max_workers = max_workers
        self.__context_retriever = ContextRetriever(
            chat_model = chat_model,
            embedding_model = embedding_model,
            reranking_model = reranking_model,
            reranking_threshold = reranking_threshold,
            max_workers= max_workers
        )

    def executor(self, user_message, config = None):
        reliable_docs, unreliable_docs, tavily_result = self.__context_retriever.get_context_for_user_message(user_message = user_message)
        prompt = [
            SystemMessage(content = LAW_GENERATION_PROMPT),
            HumanMessage(content = f"""
                RELIABLE CONTEXT: {reliable_docs} \n\n
                UNRELIABLE CONTEXT: {unreliable_docs} \n\n
                INTERNET INFORMATION: {tavily_result}
            """),
            HumanMessage(content = user_message)
        ]
        response = self.__llm.invoke(prompt)
        return response.content