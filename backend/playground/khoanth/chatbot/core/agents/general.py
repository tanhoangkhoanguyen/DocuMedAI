from playground.khoanth.chatbot.core.constants.schemas import GraphState, MessageAnalysisState, IntentAnalysisState, LocalSummarizerNode
from playground.khoanth.chatbot.core.constants.prompts import MESSAGE_ANALYSIS_PROMPT, INTENT_ANALYSIS_PROMPT, SYNTHESIS_PROMPT, LOCAL_SUMMARIZER_PROMPT
from playground.khoanth.chatbot.core.agents.chit_chat import ChitChater
from playground.khoanth.chatbot.core.agents.law_support import lawSupporter

from dotenv import load_dotenv
load_dotenv()

import os
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED
from langchain_core.messages import AIMessage

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
        return state

class NodeController(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.__llm = ChatOpenAI(model = model_name, temperature = temperature)
        self.__chit_chat = ChitChater(model_name=model_name)
        self.__law_support = lawSupporter(model_name=model_name)
        self.__synthesis_prompt = SYNTHESIS_PROMPT
        self.__instruction_support_prompt = "Thank you for your detailed instruction. The response will be revived next time."
    
    def __instruction_runner(self):
        return self.__instruction_support_prompt
    
    def __agents_runner(self, state:GraphState, max_workers:int=8, timeout:float | None = None):
        all_cpus = os.cpu_count()
        if max_workers > all_cpus - 3:
            print(f"[WARNING] From ContextRetriever: Using {all_cpus - 3} workers instead of {max_workers} by default.")
            max_workers = all_cpus - 3
        else:
            print(f"[INFO] From NodeController: Using {max_workers} workers by default.")

        answers = []
        futures = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for idx in range(len(state.messages)):
                if state.topic_id[idx] < 1:
                    f = pool.submit(
                        self.__law_support.executor, 
                        state.global_context + state.local_context + state.messages[idx]
                    )
                else:
                    f = pool.submit(
                        self.__chit_chat.executor, 
                        state.messages[idx], 
                        state.local_context, 
                        state.global_context
                    )
                futures.append(f)

            done, not_done = wait(futures, timeout=timeout, return_when=ALL_COMPLETED)

            for f in not_done:
                print(f'[ERROR] From NodeController: Multi-threading not_done. Detailed: Due to timeout {timeout}')
                f.cancel()
            
            for f in done:
                try:
                    res = f.result() 
                except Exception as e:
                    print(f"[ERROR] NodeController: Multi-threading done, but task failed: {e}")
                    continue
                answers.append(res)
        
        if not_done:
            print(f"[WARN] {len(not_done)} task(s) did not complete before timeout")

        synthesis_prompt = self.__synthesis_prompt.format(
            context_list = answers,
            instruction = state.global_instruction
        )
        ai_response = ""
        for chunk in self.__llm.stream(synthesis_prompt):
            print (chunk.content, end = "", flush = True)
            ai_response += chunk.content
        return ai_response[1:]

    def invoke(self, state:GraphState, config=None):
        try:
            if len(state.messages) != len(state.topic_id):
                raise ValueError("Mismatch len detect: state.messages and state.topic_id")
        except ValueError as e:
            print (e)
            exit (1)
        
        response = self.__instruction_runner() if len(state.messages) < 1 else self.__agents_runner(state)
        state.chat_history.append(AIMessage(content = response))
        return state

class SchemaResetNode(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.__llm = ChatOpenAI(model = model_name, temperature = temperature).with_structured_output(LocalSummarizerNode)
        self.__local_summarizer_prompt = LOCAL_SUMMARIZER_PROMPT

    def invoke(self, state:GraphState, config=None):
        local_summarizer_prompt = self.__local_summarizer_prompt.format(
            local_context = state.local_context,
            global_context = state.global_context,
            local_instruction = state.local_instruction,
            global_instruction = state.global_instruction
        )
        response = self.__llm.invoke(local_summarizer_prompt)

        state.global_context = response.context
        state.global_instruction = response.instruction
        state.messages = []
        state.local_context = ""
        state.local_instruction = ""
        state.topic_id = []
        return state



