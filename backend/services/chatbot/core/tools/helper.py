from services.chatbot.core.constants.schemas import LLMInvokeState
from services.chatbot.core.constants.prompts import PARAPHRASE_USER_MESSAGE_PROMPT, GENERALIZE_USER_MESSAGE_PROMPT, LAW_CLASSIFIER_PROMPT

from dotenv import load_dotenv
load_dotenv()

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from langsmith import traceable
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_huggingface import HuggingFaceEmbeddings
from threading import Lock

class UserMessagePreprocesser:
    def __init__(self, chat_model:str, max_workers:int, temperature:int = 0):
        self.__llm = ChatOpenAI(model = chat_model, temperature = temperature)
        self.__max_workers = max_workers

    @traceable
    def __paraphrase_user_message(self, user_message:str, number:int):
        prompt = [
            SystemMessage(content = PARAPHRASE_USER_MESSAGE_PROMPT.format(number = number)),
            HumanMessage(content = f"User's message: {user_message}")
        ]
        queries = self.__llm.with_structured_output(LLMInvokeState).invoke(prompt)
        return queries.unit

    @traceable
    def __generalize_user_message(self, user_message:str):
        prompt = [
            SystemMessage(content = GENERALIZE_USER_MESSAGE_PROMPT),
            HumanMessage(content = user_message)
        ]
        query = self.__llm.invoke(prompt)
        return query.content
    
    def rephrase_user_message(self, user_message: str, number:int = 3, timeout = None):
        with ThreadPoolExecutor(self.__max_workers) as pool:
            fut_para = pool.submit(self.__paraphrase_user_message, user_message, number)
            fut_gener = pool.submit(self.__generalize_user_message, user_message)

            para_query_resp = fut_para.result(timeout = timeout) # fut_para.cancel()
            gener_query_resp = fut_gener.result()

        return para_query_resp + [gener_query_resp]


class LawTypeIdentifier:
    def __init__(self, chat_model:str, temperature:int = 0):
        self.__llm = ChatOpenAI(model = chat_model, temperature = temperature)
    
    def identify_law_type(self, user_message):
        prompt = [
            SystemMessage(content = LAW_CLASSIFIER_PROMPT),
            HumanMessage(content = f"User Message: {user_message}")
        ]
        response = self.__llm.invoke(prompt)
        return response.content

class EmbeddingModelLoad: 
    _instance = None
    _model_name = None
    _lock = Lock()

    def get_instance(self, embedding_model): 
        if self._instance is None or self._model_name != embedding_model:
            with self._lock: 
                self._instance = HuggingFaceEmbeddings(model_name = embedding_model) 
                self._model_name = embedding_model
        return self._instance 
        
    def reset(self): 
        with self._lock: 
            self._instance = None
            self._model_name = None