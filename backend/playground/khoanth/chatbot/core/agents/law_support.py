from playground.khoanth.chatbot.core.constants.schemas import GraphState
from services.chatbot.core.tools.retrieve_context import ContextRetriever
from playground.khoanth.chatbot.core.constants.prompts import LAW_CONTEXT_QUESTION_PROMPT

from dotenv import load_dotenv
load_dotenv()
import time

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI


class lawSupporter(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.__llm = ChatOpenAI(model = model_name, temperature = temperature)
        self.__context_retriever = ContextRetriever()
        self.__law_context_question_prompt = LAW_CONTEXT_QUESTION_PROMPT

    def invoke(self, state:GraphState, config = None):
        user_message = state.global_context + state.local_context + state.messages[state.messages_idx]
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
            self.__law_context_question_prompt.format(
                reliable_context=str(reliable_docs),
                unreliable_context=str(unreliable_docs),
                website_information=str(tavily_result),
                question=user_message,
            )
        )
        end = time.time()
        responding_time = end - start
        print(f'[INFO] From LawAdvisor: Responding time: {responding_time} seconds')

        state.answers.append(response.content)
        state.messages_idx += 1
        print (f"From law_support: I am done with this question - {user_message}")
        return state