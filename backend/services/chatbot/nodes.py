import uuid
from crewai import Agent, Crew, LLM, Process, Task
from concurrent.futures import ThreadPoolExecutor
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.runnables import Runnable
# Gemini is reached through Google Vertex AI
from langchain_google_vertexai import ChatVertexAI
from pydantic import BaseModel
from typing import Any, List, Optional, Type

import warnings
warnings.filterwarnings("ignore")

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
from utils.qdrant_client import get_qdrant_client
from toolcore.core import get_mcp_client
from utils.rag import get_rag_client
from utils.pattern_cipher import get_pattern_cipher
from utils.llm_config import get_vertex_project, get_vertex_location, build_chat_model  # Vertex AI config


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
        # Routed through LLMGuard when LLM_GATEWAY_URL is set, so this node's
        # summarisation gets the gateway's retry and circuit breaking. It is the
        # only node eligible: the others call with_structured_output, which
        # LangChain implements with tool calling and LLMGuard refuses with a 400.
        self.__llm = build_chat_model(chat_model, temperature)
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
        if state.user_info:
            user_id = state.user_info.user_id
        else:
            raise ValueError("user_info is required for TopicChecker to store long-term memory")
        self.__qdrant_client.push_documents(
            LONGTERM_COLLECTION,
            ids = [self.__pattern_cipher.hash_user_id(user_id)],
            queries = None,
            embedded_queries = [embedded_resp],
            payloads = [{"query": summarized_resp, "user_id": user_id}],
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
        self.__llm = ChatVertexAI(
                model = chat_model,
                temperature = temperature,
                project = get_vertex_project(),
                location = get_vertex_location(),
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

    def __retrieve_longterm_snippet(self, unit: SubMessageState, user_id: str) -> str:
        if not user_id:
            return ""

        parts = [unit.context or "", " ".join(unit.messages or []), unit.instruction or ""]
        query = " ".join(p for p in parts if p).strip()
        if not query:
            return ""

        # Retrieve semantic longterm memory, scoped to this user (tenant isolation).
        vec = self.__qdrant_client.embed_query(query)
        resp = self.__qdrant_client.retrieve_query(
            LONGTERM_COLLECTION,
            vec,
            user_id = user_id,
            with_payload = True,   # payload holds the summary under "query"
        )

        return self.__qdrant_client.get_top_scored_payload(resp, self.__qdrant_threshold)

    def invoke(self, state: GraphState, config = None):
        if state.user_info:
            user_id = state.user_info.user_id
        else:
            raise ValueError("user_info is required for LongTermMemoryRetriever to retrieve long-term memory")
        for unit in state.user_inputs:
            if not unit.messages:
                continue
            state.task_list.append(
                TaskState(
                    context = (unit.context or "") + self.__retrieve_longterm_snippet(unit, user_id),
                    messages = [m.strip() for m in unit.messages if m and m.strip()],
                )
            )
        return state


class Agents(Runnable):
    class planner_output_schema(BaseModel):
        tool_calls: List[ToolCallState]

    @staticmethod
    def get_crew_model(chat_model: str):
        # CrewAI/litellm reaches Vertex AI via the "vertex_ai/<model>" prefix,
        # reading GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION from the env.
        if "/" in chat_model:
            return chat_model
        if chat_model.startswith(("gpt")):
            return f"openai/{chat_model}"
        return f"vertex_ai/{chat_model}"

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
        self.__llm = ChatVertexAI(
            model = chat_model,
            temperature = temperature,
            project = get_vertex_project(),
            location = get_vertex_location(),
        )
        self.__crew_llm = LLM(
            model = self.get_crew_model(chat_model),  # vertex_ai/<model>
            temperature = temperature,
            vertex_project = get_vertex_project(),
            vertex_location = get_vertex_location(),
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

    def __run_planner(self, task: TaskState, doc_hint: str = ""):
        catalog = self.__mcp_client.format_registry()
        # doc_hint is per-request (the current user's uploaded-doc description) and
        # is prepended to the planner context so the LLM router can decide whether
        # search_user_documents is relevant. It's a local arg — never stored on self —
        # so concurrent users can't see each other's document.
        context = f"{doc_hint}\n{task.context}" if doc_hint else task.context
        prompt = [
            SystemMessage(
                content = AGENT_PLANNER_PROMPT.format(
                    tool_catalog = catalog,
                    context = context,
                )
            ),
            HumanMessage(content = "USER MESSAGES:\n" + "\n".join(f"- {m}" for m in task.messages))
        ]
        return self.__llm.with_structured_output(self.planner_output_schema).invoke(prompt)

    def __process_task(self, task: TaskState, user_id: str = "", doc_hint: str = ""):
        plan = self.__run_planner(task, doc_hint)
        tool_calls = [tool_call for tool_call in plan.tool_calls if self.__mcp_client.has_tool(tool_call.tool)]
        if not tool_calls:
            return ""
        with ThreadPoolExecutor(max_workers = self.__max_workers) as pool:
            futures = [
                pool.submit(
                    self.__mcp_client.execute_tool_call,
                    tool_call.tool,
                    tool_call.message,
                    user_id,
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
        for task in task_list:
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
        user_id = state.user_info.user_id if state.user_info else ""

        # Per-request routing hint: tell the planner what the user's uploaded doc is
        # about so it can choose search_user_documents when relevant. Empty if no ready
        # doc. Read off GraphState (populated by the workspace layer) — no DB call here.
        doc_hint = ""
        if state.user_document and state.user_document.description:
            doc_hint = (
                f"The user has uploaded a document described as: "
                f"\"{state.user_document.description}\". Use search_user_documents to "
                f"retrieve from it when the question relates to that document."
            )

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
                with ThreadPoolExecutor(max_workers = self.__max_workers) as pool:
                    futures = [pool.submit(self.__process_task, task, user_id, doc_hint) for task in state.task_list]
                    for task, future in zip(state.task_list, futures):
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