from services.chatbot.core.constants.schemas import LawAgentState, TopicIDResponse
from services.chatbot.core.RAG.law_retriever import invoke_law_advisor

import asyncio
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

load_dotenv()

class LawAdvisor(Runnable):
    def __init__(self, model_name:str, temperature:int = 0):
        self.llm = ChatOpenAI(model = model_name, temperature = temperature)
        self.structured_llm = self.llm.with_structured_output(TopicIDResponse)

    def invoke(self, state:LawAgentState, config = None):
        message = state.messages[-1].content
        response = asyncio.run(invoke_law_advisor(self.llm, self.structured_llm, message))
        state.messages.append(response)
        return state