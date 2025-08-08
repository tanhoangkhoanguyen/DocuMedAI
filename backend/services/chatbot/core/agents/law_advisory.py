from services.chatbot.core.constants.schemas import LawAgentState, TopicIDResponse
from services.chatbot.core.rag.law_retriever import invoke_law_advisor
from services.chatbot.core.tools.retrieve_context import ContextRetriever
from services.chatbot.core.constants.prompts import CONTEXT_QUESTION_PROMPT

import asyncio
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
load_dotenv()

from concurrent.futures import ThreadPoolExecutor
_executor = ThreadPoolExecutor()



class LawAdvisor(Runnable):
    def __init__(self, model_name:str, temperature:int = 0):
        self.__llm = ChatOpenAI(model = model_name, temperature = temperature)
        # self.structured_llm = self.llm.with_structured_output(TopicIDResponse)
        self.__context_retriever = ContextRetriever()
        self.__context_question_prompt = CONTEXT_QUESTION_PROMPT

    def invoke(self, state:LawAgentState, config = None):
        # user_message = state.messages[-1].content
        # coro = invoke_law_advisor(self.llm, self.structured_llm, user_message)
        # future = _executor.submit(asyncio.run, coro)
        # response = future.result()
        # state.messages.append(response)
        user_message = state.messages[-1].content

        coro = self.__context_retriever.get_context_for_user_message(user_message=user_message)
        future = _executor.submit(asyncio.run(), coro)
        reliable_docs, unreliable_docs, tavily_response = future.result()
        
        response = self.__llm.invoke(self.__context_question_prompt.format(
            reliable_context = reliable_docs,
            unreliable_context = unreliable_docs,
            website_information = tavily_response,
            question = user_message
        ))
        state.messages.append(response)
        return state