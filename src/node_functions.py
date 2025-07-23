import json
from dotenv import load_dotenv, find_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, SystemMessage
from pprint import pprint


from .state_schema import State, IntOuput
from .prompt import *


_ = load_dotenv(find_dotenv())
chat = ChatOpenAI(model = 'gpt-3.5-turbo', temperature = 0)
chain = chat.with_structured_output(IntOuput, method = "function_calling")


# ========== CONDITIONAL NODES ==========
def decide_node(state):
    topic_id = state.get('topic_id', 0)
    if topic_id == 0:
        return "conversational_management"
    elif topic_id == 1:
        return "problem_resolution"


# ========== NODES ==========
def intent_detector(state):
    message = state['messages'][-1].content
    intentDetector_prompt = INTENT_DETECTOR_PROMPT.format(message = message)
    response = chain.invoke(intentDetector_prompt)
    
    state['topic_id'] = response['id']
    return state