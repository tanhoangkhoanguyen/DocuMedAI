import json
from dotenv import load_dotenv, find_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, SystemMessage
from pprint import pprint


from src.state_schema import State, IntOuput
from .conversational_prompt import *


_ = load_dotenv(find_dotenv())
chat = ChatOpenAI(model = 'gpt-3.5-turbo', temperature = 0)
chain = chat.with_structured_output(IntOuput, method = "function_calling")


EMAIL = "hoangkhoa.nguyentan@gmail.com"


# ========== CONDITIONAL NODES ==========
def conversational_decide_note(state):
    topic_id = state.get('topic_id', 0)
    if topic_id == 0:
        return "greeting"
    elif topic_id == 1:
        return "add_instruction"
    elif topic_id == 2:
        return "complain_contact"


# ========== NODES ==========
def conversational_intent_detector(state):
    message = state['messages'][-1].content
    intentDetector_prompt = CONVERSATIONAL_INTENT_DETECTOR_PROMPT.format(message = message)
    response = chain.invoke(intentDetector_prompt)
    
    state['topic_id'] = response['id']
    return state


def greeting(state):
    greeting_prompt = "Hi, I am your law advisory chatbot. What can I do for you?"
    print (greeting_prompt)
    return {'messages': [
        SystemMessage(content = greeting_prompt)
    ]}


def add_instruction(state):
    sys_msg = "Thank you for your instruction. The response will be revived next time."
    print (sys_msg)

    message = state['messages'][-1].content
    addInstruction_prompt = ADD_INSTRUCTION_PROMPT.format(input_message = message)
    response = chat.invoke(addInstruction_prompt)

    return {
        'messages': [SystemMessage(content = sys_msg)],
        'user_intruction': response
        }


def complain_contact(state):
    complainContact_prompt = f"""I am sicerely sorry for this inconvenient.
Please reach out to me through my:
- email: {EMAIL}
    """
    print (complainContact_prompt)
    return {'messages': [
        SystemMessage(content = complainContact_prompt)
    ]}