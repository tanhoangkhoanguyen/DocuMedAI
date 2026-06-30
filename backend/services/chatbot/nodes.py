from crewai import Agent, Crew, LLM, Process, Task
from concurrent.futures import ThreadPoolExecutor
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.runnables import Runnable
# Gemini is reached through the Go LLM proxy via its OpenAI-compatible endpoint
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from typing import Any, List, Optional, Type

import os, warnings
warnings.filterwarnings("ignore")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

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
from vector_database_tests.utils.qdrant_client import get_qdrant_client
from services.chatbot.mcp import get_mcp_client
from services.chatbot.tools.rag import get_rag_client
from services.chatbot.tools.pattern_cipher import get_pattern_cipher
from services.chatbot.tools.llm_config import get_llm_base_url  # route LLM calls via the Go proxy


LONGTERM_COLLECTION = "LongtermMemory"


class TopicChecker(Runnable):
    def __init__(
            self, 
            chat_model: str,
            temperature: float,
            topic_threshold: float,
            embedding_model: str,
            embedding_dimension: int,
            reranking_model: str,
            reranking_threshold: float,
        ):
        self.__llm = ChatOpenAI(
                model = chat_model,
                temperature = temperature,
                base_url = get_llm_base_url(),  # → Go LLM proxy → Gemini (OpenAI-compat)
                api_key = GEMINI_API_KEY,
            )
        self.__rag_client = get_rag_client(
            chat_model = chat_model,
            temperature = temperature,
            reranking_model = reranking_model,
            reranking_threshold = reranking_threshold,
        )
        self.__qdrant_client = get_qdrant_client(
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension,
        )
        self.__pattern_cipher = get_pattern_cipher()
        self.__topic_threshold = topic_threshold

    def invoke(self, state: GraphState, config = None):
        shortterm_memories = state.shortterm_memory or []
        compressed = "\n".join(shortterm_memories)
        user_msg = state.chat_history[-1].content

        if self.__rag_client.same_topic(user_msg, compressed, self.__topic_threshold):
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
            [self.__pattern_cipher.hash_user_id(state.user_info.username)],
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
        self.__llm = ChatOpenAI(
                model = chat_model,
                temperature = temperature,
                base_url = get_llm_base_url(),  # → Go LLM proxy → Gemini (OpenAI-compat)
                api_key = GEMINI_API_KEY,
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
            max_workers: int,
            embedding_model: str,
            embedding_dimension: int,
        ):
        self.__qdrant_client = get_qdrant_client(
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension,
        )
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

        return self.__qdrant_client.get_top_scored_payload(resp, self.__qdrant_threshold)

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

    @staticmethod
    def get_crew_model(chat_model: str):
        if "/" in chat_model:
            return chat_model
        if get_llm_base_url() or chat_model.startswith(("gpt")):
            return f"openai/{chat_model}"
        if chat_model.startswith(("gemini")):
            return f"gemini/{chat_model}"

    def __init__(
            self,
            chat_model: str,
            temperature: float,
            max_workers: int,
            shortterm_memory_size: int,
            max_revision_cycles: int,
            embedding_model: str,
            embedding_dimension: int,
            reranking_model: str,
            reranking_threshold: float,
        ):
        self.__llm = ChatOpenAI(
            model = chat_model,
            temperature = temperature,
            base_url = get_llm_base_url(),  # → Go LLM proxy → Gemini (OpenAI-compat)
            api_key = GEMINI_API_KEY,
        )
        _crew_base_url = get_llm_base_url()
        _crew_kwargs = {"base_url": _crew_base_url} if _crew_base_url else {}
        self.__crew_llm = LLM(
            model = self.get_crew_model(chat_model),
            temperature = temperature,
            api_key = GEMINI_API_KEY,
            **_crew_kwargs,
        )
        self.__max_workers = max_workers
        self.__mcp_client = get_mcp_client(
            chat_model = chat_model,
            temperature = temperature,
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension,
            reranking_model = reranking_model,
            reranking_threshold = reranking_threshold,
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
        return self.__llm.with_structured_output(self.planner_output_schema).invoke(prompt)

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

    @staticmethod
    def __parse_crew_output(model_cls: Type[BaseModel], raw: Any) -> Optional[BaseModel]:
        """
        CrewAI Task.output is TaskOutput class
        """
        if raw is None:
            return None
        if isinstance(raw, model_cls):
            return raw
        for x in (
            getattr(raw, "pydantic", None),
            getattr(raw, "json_dict", None),
            getattr(raw, "raw", None),
            raw,
        ):
            if isinstance(x, dict) and x:
                try:
                    return model_cls.model_validate(x)
                except Exception:
                    pass
            elif isinstance(x, str) and x.strip():
                try:
                    return model_cls.model_validate_json(x.strip())
                except Exception:
                    pass
        if isinstance(raw, BaseModel):
            try:
                return model_cls.model_validate(raw.model_dump())
            except Exception:
                pass
        return None

    def invoke(self, state: GraphState, config = None):
        long_term_memory = self.__get_long_term_memory(state.task_list)
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

            parsed_draft = self.__parse_crew_output(DraftAgentState, getattr(draft_task, "output", None))
            if parsed_draft and parsed_draft.reply_text:
                last_reply = parsed_draft.reply_text

            parsed_critic = self.__parse_crew_output(CritiqueAgentState, getattr(critic_task, "output", None))
            if parsed_critic and parsed_critic.pass_fail == "pass":
                break
            revision_notes = parsed_critic.feedback if parsed_critic else ""

        state.chat_history.append(AIMessage(content = last_reply))
        return state


class SchemaUpdater(Runnable):
    def __init__(self, shortterm_memory_size: int):
        self.__shortterm_memory_size = shortterm_memory_size

    def invoke(self, state: GraphState, config = None):
        state.user_inputs = state.task_list = None
        state.shortterm_memory.append(f"User Message: {state.chat_history[-2].content}\nAI Message: {state.chat_history[-1].content}")
        if (len(state.shortterm_memory) > self.__shortterm_memory_size):
            state.shortterm_memory.pop(0)
        return state