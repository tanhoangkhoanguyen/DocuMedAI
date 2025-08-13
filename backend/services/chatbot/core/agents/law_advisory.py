from services.chatbot.core.constants.schemas import LawAgentState
from services.chatbot.core.tools.retrieve_context import ContextRetriever
from services.chatbot.core.constants.prompts import CONTEXT_QUESTION_PROMPT

from dotenv import load_dotenv
load_dotenv()
import time

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI



class LawAdvisor(Runnable):
    def __init__(self, model_name:str, temperature:int = 0):
        self.__llm = ChatOpenAI(model = model_name, temperature = temperature)
        self.__context_retriever = ContextRetriever()
        self.__context_question_prompt = CONTEXT_QUESTION_PROMPT

    def invoke(self, state:LawAgentState, config = None):
        user_message = state.messages[-1].content
        start = time.time()
        try:
            reliable_docs, unreliable_docs, tavily_result = self.__context_retriever.get_context_for_user_message(
                user_message=user_message
            )
            print(f'[INFO] Successfully get context for user message.')
        except Exception as e:
            print(f"[ERROR] From LawAdvisor: Fail to get context for user message. Detailed error information: {str(e)}")
        end = time.time()
        retrieving_time = end - start
        
        print(f'[INFO] From LawAdvisor: Retrieving time: {retrieving_time} seconds')
        start = time.time()
        # If these are lists/objects, cast to string before format
        response = self.__llm.invoke(
            self.__context_question_prompt.format(
                reliable_context=str(reliable_docs),
                unreliable_context=str(unreliable_docs),
                website_information=str(tavily_result),
                question=user_message,
            )
        )
        end = time.time()
        responding_time = end - start
        print(f'[INFO] From LawAdvisor: Responding time: {responding_time} seconds')

        state.messages.append(response)

        return state