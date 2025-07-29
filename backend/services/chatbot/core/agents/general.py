from services.chatbot.core.constants.schemas import LawAgentState, TopicIDResponse
from services.chatbot.core.constants.prompts import INTENT_DETECTOR_PROMPT

from dotenv import load_dotenv
load_dotenv()

from langchain_core.runnables import Runnable
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

class IntentDetector(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.llm = ChatOpenAI(model=model_name, temperature=temperature).with_structured_output(TopicIDResponse)
        self.intent_detector_prompt = INTENT_DETECTOR_PROMPT
    
    def invoke(self, state:LawAgentState, config=None):
        message = state.messages[-1].content
        prompt = self.intent_detector_prompt.format(message=message)
        response = self.llm.invoke(prompt)
        state.topic_id = response.model_dump().get('id', 10)
        return state

class Greetor(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.greeting_prompt = "Hi, I am your law advisory chatbot. What can I do for you?"

    def invoke(self, state:LawAgentState, config=None):
        state.messages.append(AIMessage(content=self.greeting_prompt))
        return state

class ComplaintSupporter(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.complaint_response = "I am sincerely sorry for this inconvenient. I will send this problems to the support teams and they will contact you as soon as possible."

    def invoke(self, state:LawAgentState, config=None):
        state.messages.append(AIMessage(content=self.complaint_response))
        return state

class ChitChater(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.llm = ChatOpenAI(model=model_name, temperature=temperature)

    def invoke(self, state:LawAgentState, config=None):
        message = state.messages[-1].content
        response = self.llm.invoke(message)
        state.messages.append(response)
        return state

class InstructionSupporter(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.instruction_response = "Thank you for your detailed instruction. The response will be revived next time."
    
    def invoke(self, state:LawAgentState, config=None):
        state.messages.append(AIMessage(content=self.instruction_response))
        return state
        