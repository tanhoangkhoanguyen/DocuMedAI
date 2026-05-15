from crewai import Agent, Crew, LLM, Process, Task
from concurrent.futures import ThreadPoolExecutor
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI
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
    DraftAgentState,
    CritiqueAgentState
)
from services.chatbot.constants.prompts import (
    TOPIC_SUMMARIZED_PROMPT,
    MESSAGE_ANALYSIS_PROMPT_1,
    MESSAGE_ANALYSIS_PROMPT_2,
    AGENT_PLANNER_PROMPT,
    DRAFT_AGENT_PROMPT,
    CRITIC_AGENT_PROMPT
)
from vector_database_tests.utils.qdrant_client import QdrantClient
from services.chatbot.mcp import MCPServer
from services.chatbot.tools.rag import RAG
from services.chatbot.tools.pattern_cipher import PatternCipher


LONGTERM_COLLECTION = "LongtermMemory"
_SHARED_QDRANT_CLIENT = QdrantClient(
    embedding_model = "sentence-transformers/all-MiniLM-L6-v2",
    embedding_dimension = 384
)
_SHARED_PATTERN_CIPHER = PatternCipher


class TopicChecker(Runnable):
    def __init__(
            self, 
            chat_model: str,
            temperature: float,
            reranking_model: str,
            topic_threshold: float,
        ):
        self.__rag_client = RAG(
            chat_model = chat_model,
            temperature = temperature,
            reranking_model = reranking_model,
            reranking_threshold = topic_threshold
        )
        self.__llm = ChatGoogleGenerativeAI(
                model = chat_model,
                temperature = temperature
            )
        self.__qdrant_client = _SHARED_QDRANT_CLIENT
        self.__pattern_cipher = _SHARED_PATTERN_CIPHER

    def invoke(self, state: GraphState, config = None):
        shortterm_memories = state.shortterm_memory or []
        compressed = "\n".join(shortterm_memories)
        user_msg = state.chat_history[-1].content

        if self.__rag_client.same_topic(user_msg, compressed):
            return state

        if not shortterm_memories:
            return state

        prompt = [
            SystemMessage(
                content = TOPIC_SUMMARIZED_PROMPT.format(conversation = compressed)
            ),
            HumanMessage(
                content = "Summarize the conversation from the system message into durable long-term memory text only."
            ),
        ]
        response = self.__llm.invoke(prompt)
        summarized_resp = response.content
        embedded_resp = self.__qdrant_client.embed_query(summarized_resp)
        self.__qdrant_client.push_documents(
            LONGTERM_COLLECTION,
            [self.__pattern_cipher.encode_user_id(state.user_info.user_name)],
            [summarized_resp],
            [embedded_resp],
        )
        state.shortterm_memory = []
        return state


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
                model = chat_model,
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
        state.user_inputs = response.user_inputs or []
        return state


class LongTermMemoryRetriever(Runnable):
    """
    Retrieve long term memory
    """

    def __init__(
            self,
            qdrant_threshold: float,
            max_workers: int
        ):
        self.__qdrant_client = _SHARED_QDRANT_CLIENT
        self.__qdrant_threshold = qdrant_threshold
        self.__max_workers = max_workers

    def __retrieve_longterm_snippet(self, query: str) -> str:
        if not query or not str(query).strip():
            return ""

        # Retrieve semantic longterm memory
        vec = self.__qdrant_client.embed_query(query)
        resp = self.__qdrant_client.retrieve_query(
            LONGTERM_COLLECTION,
            vec
        )

        # Evaluate memory
        points = getattr(resp, "points", None)
        if points is None and isinstance(resp, dict):
            points = resp.get("points")
        if not points:
            return ""
        p0 = points[0]
        score = getattr(p0, "score", None)
        if score is None and isinstance(p0, dict):
            score = p0.get("score", 0.0)
        if score is None or float(score) < self.__qdrant_threshold:
            return ""
        payload = getattr(p0, "payload", None) or {}
        if not isinstance(payload, dict):
            payload = {}
        text = payload.get("query") or payload.get("text") or ""
        return str(text).strip() if text else ""

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
            state.task_list.append([
                TaskState(
                        context = mem,
                        message = msg,
                    )
                for mem, msg in zip(memories, unit.messages)
            ])
        return state


class Agents(Runnable):
    class planner_output_schema(BaseModel):
        tool_calls: List[ToolCallState]

    def __init__(
            self,
            chat_model: str,
            temperature: float,
            embedding_model: str,
            embedding_dimension: int,
            reranking_model: str,
            rag_threshold: float,
            max_workers: int,
            shortterm_memory_size: int,
            max_revision_cycles: int,
        ):
        self.__llm = ChatGoogleGenerativeAI(
            model = chat_model,
            temperature = temperature,
        )
        crew_model = chat_model if "/" in chat_model else f"gemini/{chat_model}"
        self.__crew_llm = LLM(
            model = crew_model,
            temperature = temperature,
        )
        self.__max_workers = max_workers
        self.__mcp_client = MCPServer(
            chat_model = chat_model,
            temperature = temperature,
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension,
            reranking_model = reranking_model,
            rag_threshold = rag_threshold,
        )
        self.__shortterm_memory_size = shortterm_memory_size
        self.__max_revision_cycles = max_revision_cycles

    def __run_planner(self, task: TaskState):
        catalog = self.__mcp_client.format_registry()
        prompt = [
            SystemMessage(
                content = AGENT_PLANNER_PROMPT.format(
                    tool_catalog = catalog,
                    context = task.context,
                )
            ),
            HumanMessage(content = f"USER MESSAGE:\n{task.message}")
        ]
        return self.__llm.with_structured_output(Agents.planner_output_schema).invoke(prompt)

    def __process_task(self, task: TaskState):
        plan = self.__run_planner(task)
        tool_calls = [tool_call for tool_call in plan.tool_calls if self.__mcp_client.has_tool(tool_call.tool)]
        if not tool_calls:
            return ""
        with ThreadPoolExecutor(max_workers = self.__max_workers) as pool:
            futures = [
                pool.submit(
                    self.__mcp_client.execute_tool_call,
                    tool_call.tool,
                    tool_call.message,
                )
                for tool_call in tool_calls
            ]
            results = [f.result() for f in futures]
        return "\n".join(results)

    @staticmethod
    def __get_long_term_memory(task_list) -> str:
        if not task_list:
            return ""
        seen: set[str] = set()
        out: List[str] = []
        for tasks in task_list:
            for task in tasks:
                ctx = (task.context or "").strip()
                if not ctx or ctx in seen:
                    continue
                seen.add(ctx)
                out.append(ctx)
        return '\n'.join(out) if out else ""

    def invoke(self, state: GraphState, config = None):
        long_term_memory = Agents.__get_long_term_memory(state.task_list)
        shortterm_memories = state.shortterm_memory or []
        shortterm_memory = "\n".join(shortterm_memories[-1 * self.__shortterm_memory_size:])
        user_message = state.chat_history[-1].content

        draft_agent = Agent(
            role = "Draft Writer",
            goal = "Produce user-facing replies grounded only in supplied evidence.",
            backstory = "Never invent facts absent from the evidence bundle.",
            llm = self.__crew_llm,
            verbose = False,
            allow_delegation = False,
        )

        critic_agent = Agent(
            role = "Critic",
            goal = "Emit structured pass/fail critiques.",
            backstory = "Detect hallucinations, incorrect output format and missing retrieval.",
            llm = self.__crew_llm,
            verbose = False,
            allow_delegation = False,
        )

        mcp_outputs = ""
        revision_notes = ""
        last_reply = ""
        for _ in range (self.__max_revision_cycles):
            if state.task_list:
                # Update mcp_outputs
                for idx, tasks in enumerate(state.task_list):
                    with ThreadPoolExecutor(max_workers = self.__max_workers) as pool:
                        futures = [pool.submit(self.__process_task, task) for task in tasks]
                        for task, future in zip(state.task_list[idx], futures):
                            task.result = future.result()
                            mcp_outputs += f"\n{task.result or ''}"

            draft_task = Task(
                description = DRAFT_AGENT_PROMPT.format(
                    evidence = mcp_outputs,
                    long_term_memory = long_term_memory,
                    shortterm_memory = shortterm_memory,
                    user_message = user_message,
                    revision_notes = revision_notes,
                ),
                expected_output = "DraftOutput JSON.",
                agent = draft_agent,
                output_pydantic = DraftAgentState,
            )
            critic_task = Task(
                description = CRITIC_AGENT_PROMPT.format(
                    evidence = mcp_outputs,
                    long_term_memory = long_term_memory,
                    shortterm_memory = shortterm_memory,
                    user_message = user_message,
                ),
                expected_output = "CritiqueOutput JSON.",
                agent = critic_agent,
                output_pydantic = CritiqueAgentState,
                context = [draft_task],
            )
            tasks_crew = [draft_task, critic_task]
            agents_crew = [draft_agent, critic_agent]

            crew = Crew(
                agents = agents_crew,
                tasks = tasks_crew,
                process = Process.sequential,
                verbose = False,
            )
            try:
                crew.kickoff()
            except Exception:
                break

            draft_out = getattr(draft_task, "output", None)
            if isinstance(draft_out, DraftAgentState) and draft_out.reply_text:
                last_reply = draft_out.reply_text

            critic_out = getattr(critic_task, "output", None)
            if isinstance(critic_out, CritiqueAgentState) and critic_out.pass_fail == "pass":
                break
            revision_notes = critic_out.feedback if isinstance(critic_out, CritiqueAgentState) else ""

        return {"chat_history": [AIMessage(content = last_reply)]}


class SchemaUpdater(Runnable):
    def __init__(self, shortterm_memory_size: int):
        self.__shortterm_memory_size = shortterm_memory_size

    def invoke(self, state: GraphState, config = None):
        state.user_inputs = state.task_list = None
        state.shortterm_memory.append(f"User Message: {state.chat_history[-2].content}\nAI Message: {state.chat_history[-1].content}")
        if (len(state.shortterm_memory) > self.__shortterm_memory_size):
            state.shortterm_memory.pop(0)
        return state