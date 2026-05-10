from concurrent.futures import ThreadPoolExecutor
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI
# from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from typing import List

import warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()

from services.chatbot.constants.schemas import (
    GraphState,
    SubMessageState,
    TaskState,
    ToolCallState,
)
from services.chatbot.constants.prompts import (
    TOPIC_SUMMARIZED_PROMPT,
    MESSAGE_ANALYSIS_PROMPT_1,
    MESSAGE_ANALYSIS_PROMPT_2,
    AGENT_PLANNER_PROMPT,
    AGGREGATE_AND_REVISE_PROMPT
)
from vector_database_tests.utils.qdrant_client import QdrantClient
from services.chatbot.mcp import MCPServer
from services.chatbot.tools.rag import RAG


LONGTERM_COLLECTION = "LongtermMemory"
_SHARED_QDRANT_CLIENT = QdrantClient(
    embedding_model = "sentence-transformers/all-MiniLM-L6-v2",
    embedding_dimension = 384
)


class TopicChecker(Runnable):
    def __init__(
            self, 
            chat_model: str,
            temperature: float,
            reranking_model: str,
            topic_threshold: float,
        ):
        self.__rag_client = RAG()
        self.__llm = ChatGoogleGenerativeAI(
                model_name = chat_model,
                temperature = temperature
            )
        self.__qdrant_client = _SHARED_QDRANT_CLIENT

    def invoke(self, state: GraphState, config = None):
        if not self.__rag_client.rerank_queries(['\n'.join(state.shortterm_memory)]):
            prompt = [
                SystemMessage(content = TOPIC_SUMMARIZED_PROMPT.format(
                    conversation = '\n'.join(state.shortterm_memory)
                ))
            ]
            response = self.__llm.invoke(prompt)
            embedded_response = self.__qdrant_client.embed_query(response)
            self.__qdrant_client.push_documents([embedded_response])
            state.shortterm_memory = []
        return state


# Reasoning mode
class MessageAnalysis(Runnable):
    """
    Analyze message into submessages with
    - context
    - requests
    - instruction
    """

    class model_analysis_output_schema(BaseModel):
        user_inputs: List[SubMessageState]

    def __init__(self, chat_model: str, temperature: float):
        self.__llm = ChatGoogleGenerativeAI(
                model_name = chat_model,
                temperature = temperature
            )

    def invoke(self, state: GraphState, config = None):
        user_message = state.chat_history[-1].content
        prompt = [
            SystemMessage(content = MESSAGE_ANALYSIS_PROMPT_1),
            SystemMessage(content = MESSAGE_ANALYSIS_PROMPT_2), 
            HumanMessage(content = f"USER MESSAGE: {user_message}")
        ]
        response = self.__llm.with_structured_output(MessageAnalysis.model_analysis_output_schema).invoke(prompt)
        state.user_inputs = response.user_inputs
        return state


class LongTermMemoryRetriever(Runnable):
    """
    Retrieve long term memory
    """

    def __init__(
            self,
            qdrant_threshold: int,
            max_workers: int
        ):
        self.__qdrant_client = _SHARED_QDRANT_CLIENT
        self.__qdrant_threshold = qdrant_threshold
        self.__max_workers = max_workers

    def __retrieve_longterm_snippet(self, query: str) -> str:
        if not query:
            return ""

        # Retrieve semantic longterm memory
        vec = self.__qdrant_client.embed_query(query)
        resp = self.__qdrant_client.retrieve_query(
            LONGTERM_COLLECTION,
            vec
        )

        # Evaluate memory
        if resp["points"][0]["score"] < self.__qdrant_threshold:
            return ""
        payload = resp["points"][0].get("payload", {})
        text = payload.get("query") or payload.get("text") or ""
        return str(resp).strip() if text else ""

    def __search_memory(self, unit: SubMessageState) -> List[str]:
        ctx = (unit.context or "").strip()

        if ctx:
            blob = self.__retrieve_longterm_snippet(ctx)
            return [blob] * len(unit.messages)

        with ThreadPoolExecutor(max_workers = self.__max_workers) as pool:
            futures = [
                pool.submit(self.__retrieve_longterm_snippet, msg.strip())
                for msg in unit.messages
            ]
            return [f.result() for f in futures]
    
    def invoke(self, state: GraphState, config = None):
        for unit in state.user_inputs:
            if not unit.messages:
                continue
            memories = self.__search_memory(unit)
            state.append([
                TaskState(
                        context = mem, 
                        message = msg,
                        feedback = None,
                        result = None
                    )
                for mem, msg in zip(unit.messages, memories)
            ])
        return state


class Agent(Runnable):
    """
    Planner -> MCP tool execution (parallel over tasks) -> per-unit aggregation + revision.
    """

    class planner_output_schema(BaseModel):
        tool_calls: List[ToolCallState]

    def __init__(
        self,
        chat_model: str,
        temperature: float,
        max_workers: int,
    ):
        self.__llm = ChatGoogleGenerativeAI(
            model = chat_model,
            temperature = temperature,
        )
        self.__max_workers = max_workers
        self.__mcp_client = MCPServer()

    def __run_planner(self, task: TaskState):
        catalog = self.__mcp_client.format_registry()
        prompt = [
            SystemMessage(
                content = AGENT_PLANNER_PROMPT.format(
                    tool_catalog = catalog,
                    context = task.context or "",
                    message = task.message or "",
                )
            )
        ]
        return self.__llm.with_structured_output(Agent.planner_output_schema).invoke(prompt)

    def __process_task(self, task: TaskState):
        plan = self.__run_planner(task)
        if not plan.tool_calls:
            task.result = ""
            return
        with ThreadPoolExecutor(max_workers = self.__max_workers) as pool:
            futures = [
                pool.submit(
                    self.__mcp_client.execute_tool_call,
                    tool_call.tool,
                    tool_call.message,
                )
                for tool_call in plan.tool_calls
            ]
            results = [f.result() for f in futures]
        task.result = "\n".join(results)

    def __aggregate_results(self, state: GraphState, tool_results: str):
        prompt = [
            SystemMessage(
                content = AGGREGATE_AND_REVISE_PROMPT.format(
                    user_message = state.chat_history[-1].content,
                    shortterm_memory = '\n'.join(state.shortterm_memory)
                )
            )
        ]
        return self.__llm.invoke(prompt)

    def invoke(self, state: GraphState, config = None):
        if state.task_list:
            for idx, tasks in enumerate(state.task_list):
                unit_syntheses: List[str] = []
                with ThreadPoolExecutor(max_workers = self.__max_workers) as pool:
                    futures = [pool.submit(self.__process_task, task) for task in tasks]
                    for task, future in zip(state.task_list[idx], futures):
                        task.result = future.result()
                        unit_syntheses.append(task.result)
        state.chat_history.append(self.__aggregate_results("\n\n".join(unit_syntheses)))
        return state


class Evaluator(Runnable):
    def __init__(self):
        pass

    def invoke(self, state: GraphState, config = None):
        # TODO: CrewAI
        return state


class SchemaUpdater(Runnable):
    def __init__(
            self,
            shortterm_memory_size: int
        ):
        self.__shortterm_memory_size = 5

    def invoke(self, state: GraphState, config = None):
        state.user_inputs = state.task_list = None
        state.shortterm_memory.append(f"User Message: {state.chat_history[-2].content}\nAI Message: {state.chat_history[-1].content}")
        if (len(state.shortterm_memory) > self.__shortterm_memory_size):
            state.shortterm_memory.pop(0)
        return state