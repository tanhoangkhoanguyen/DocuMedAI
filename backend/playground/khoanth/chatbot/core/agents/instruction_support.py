from playground.khoanth.chatbot.core.constants.schemas import GraphState
from playground.khoanth.chatbot.core.constants.prompts import INSTRUCTION_SUPPORTER_PROMPT

from dotenv import load_dotenv
load_dotenv()

from langchain_core.runnables import Runnable
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

class InstructionSupporter(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.__llm = ChatOpenAI(model = model_name, temperature = temperature)
        self.__instruction_supporter_prompt = INSTRUCTION_SUPPORTER_PROMPT
        self.__instruction_response_prompt = "Thank you for your detailed instruction. The response will be revived next time."
    
    def invoke(self, state:GraphState, config=None):
        state.chat_history.append(AIMessage(content=self.__instruction_response_prompt))
        prompt = self.__instruction_supporter_prompt.format(
            global_instruction = state.global_instruction,
            local_instruction = state.local_instruction
        )
        response = self.__llm.invoke(prompt)
        state.global_instruction = response.content
        state.local_context = ""
        return state