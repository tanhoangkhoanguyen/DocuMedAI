from playground.khoanth.chatbot.core.constants.schemas import GraphState, MessageAnalysisState, IntentAnalysisState
from playground.khoanth.chatbot.core.constants.prompts import MESSAGE_ANALYSIS_PROMPT, INTENT_ANALYSIS_PROMPT, CONTEXT_UPDATE_PROMPT, SYNTHESIS_PROMPT

from dotenv import load_dotenv
load_dotenv()

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI

class MessageAnalysis(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.__llm = ChatOpenAI(model = model_name, temperature = temperature)
        self.__message_analysis_prompt = MESSAGE_ANALYSIS_PROMPT
        self.__intent_analysis_prompt = INTENT_ANALYSIS_PROMPT

    def invoke(self, state:GraphState, config=None):
        user_message = state.chat_history[-1].content

        msg_prompt = self.__message_analysis_prompt.format(message=user_message)
        msg_response = self.__llm.with_structured_output(MessageAnalysisState).invoke(msg_prompt)

        intent_prompt = self.__intent_analysis_prompt.format(
            global_context = state.global_context,
            local_context = msg_response.context,
            messages = msg_response.messages
        )
        intent_response = self.__llm.with_structured_output(IntentAnalysisState).invoke(intent_prompt)

        state.messages = msg_response.messages
        state.local_context = msg_response.context
        state.local_instruction = msg_response.instruction
        state.topic_id = intent_response.intent
        state.messages_idx = 0
        print (state.topic_id)
        return state

class NodeController(Runnable):
    def __init__(self):
        return

    def invoke(self, state:GraphState, config=None):
        return state

class Synthesis(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.__llm = ChatOpenAI(model = model_name, temperature = temperature)
        self.__synthesis_prompt = SYNTHESIS_PROMPT
        self.__context_update_prompt = CONTEXT_UPDATE_PROMPT
        self.__max_token = 500

    def invoke(self, state:GraphState, config=None):
        user_message = ' '.join(state.messages) + '\n' + state.local_context
        synthesis_prompt = self.__synthesis_prompt.format(
            context_list = state.answers,
            instruction = state.global_instruction if state.local_instruction == "" else state.local_instruction
        )
        ai_response = self.__llm.invoke(synthesis_prompt)

        context_update_prompt = self.__context_update_prompt.format(
            user_message = user_message,
            ai_message = ai_response.content,
            chat_history = state.global_context
        )
        updated_global_context = self.__llm.invoke(
            context_update_prompt, 
            config = {"max_output_tokens": self.__max_token}
        )

        state.chat_history.append(ai_response)
        state.global_context = updated_global_context.content
        return state

class SchemaResetNode(Runnable):
    def __init__(self):
        return

    def invoke(self, state:GraphState, config=None):
        state.messages = ""
        state.local_context = ""
        state.local_instruction = ""
        state.topic_id = ""
        state.messages_idx = None
        state.answers = []
        return state



