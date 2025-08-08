from services.chatbot.core.constants.schemas import LawAgentState, TopicIDResponse
from playground.khoanth.rag.law_retriever import invoke_law_advisor

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
        Nhi = asyncio.run(invoke_law_advisor(self.llm, self.structured_llm, message))
        return Nhi

if __name__ == "__main__":
    Nhi = LawAdvisor("gpt-4o-mini")
    initial_state = LawAgentState(
        messages = [HumanMessage(content = "Are there legal limits on how much wastewater a factory can release into a river?")]
    )
    resp = Nhi.invoke(initial_state)
    print (resp)