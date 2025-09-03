from playground.khoanth.chatbot.core.constants.schemas import GraphState
from playground.khoanth.chatbot.core.constants.prompts import CHIT_CHAT_PROMPT

from dotenv import load_dotenv
load_dotenv()

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

class ChitChater(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.__llm = ChatOpenAI(model=model_name, temperature=temperature)
        self.__chit_chat_prompt = CHIT_CHAT_PROMPT

    def invoke(self, state:GraphState, config=None) -> GraphState:
        user_message = state.messages[state.messages_idx]
        prompt = self.__chit_chat_prompt.format(
            user_message = user_message,
            local_context = state.local_context,
            global_context = state.global_context
        )
        response = self.__llm.invoke(prompt)
        state.answers.append(response.content)
        state.messages_idx += 1
        return state