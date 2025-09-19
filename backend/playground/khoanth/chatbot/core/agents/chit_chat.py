from playground.khoanth.chatbot.core.constants.schemas import GraphState
from playground.khoanth.chatbot.core.constants.prompts import CHIT_CHAT_PROMPT

from dotenv import load_dotenv
load_dotenv()

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

class ChitChater:
    def __init__(self, model_name:str, temperature:int=0):
        self.__llm = ChatOpenAI(model=model_name, temperature=temperature)
        self.__chit_chat_prompt = CHIT_CHAT_PROMPT

    def executor(self, user_message, local_context, global_context, config=None) -> GraphState:
        prompt = self.__chit_chat_prompt.format(
            user_message = user_message,
            local_context = local_context,
            global_context = global_context
        )
        response = self.__llm.invoke(prompt)
        return response.content