from services.chatbot.core.constants.schemas import TopicIDResponse, LawAgentState

from dotenv import load_dotenv
load_dotenv()
import os

from langchain_core.runnables import Runnable
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

class LawAdvisor(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.law_response = "Currently, I can not answer any questions about Law"
        self.llm = ChatOpenAI(model=model_name, temperature=temperature).with_structured_output(TopicIDResponse)

    def invoke(self, state:LawAgentState, config=None):
        state.messages.append(AIMessage(content=self.law_response))
        return state
