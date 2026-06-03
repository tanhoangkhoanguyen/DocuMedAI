from services.chatbot.constants.schemas import GraphState
from services.chatbot.nodes import (
    Agents,
    LongTermMemoryRetriever,
    MessageAnalysis,
    SchemaUpdater,
    TopicChecker,
)

import warnings
warnings.filterwarnings("ignore")

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver


class GraphBuilder:
    def __init__(
            self,
            chat_model: str,
            temperature: float,
            embedding_model: str,
            embedding_dimension: int,
            reranking_model: str,
            reranking_threshold: float,
            max_workers: int,
            topic_threshold: float,
            qdrant_threshold: float,
            shortterm_memory_size: int,
            max_revision_cycles: int,
        ):
        self.builder = StateGraph(GraphState)
        self.chat_model = chat_model
        self.temperature = temperature
        self.embedding_model = embedding_model
        self.embedding_dimension = embedding_dimension
        self.reranking_model = reranking_model
        self.reranking_threshold = reranking_threshold
        self.max_workers = max_workers
        self.topic_threshold = topic_threshold
        self.qdrant_threshold = qdrant_threshold
        self.shortterm_memory_size = shortterm_memory_size
        self.max_revision_cycles = max_revision_cycles

    def build_graph(self):
        self.topic_checker = TopicChecker(
            chat_model = self.chat_model,
            temperature = self.temperature,
            topic_threshold = self.topic_threshold,
            embedding_model = self.embedding_model,
            embedding_dimension = self.embedding_dimension,
            reranking_model = self.reranking_model,
            reranking_threshold = self.reranking_threshold,
        )
        self.message_analysis = MessageAnalysis(
            chat_model = self.chat_model,
            temperature = self.temperature,
        )
        self.long_term_memory_retriever = LongTermMemoryRetriever(
            qdrant_threshold = self.qdrant_threshold,
            max_workers = self.max_workers,
            embedding_model = self.embedding_model,
            embedding_dimension = self.embedding_dimension,
        )
        self.agents = Agents(
            chat_model = self.chat_model,
            temperature = self.temperature,
            max_workers = self.max_workers,
            shortterm_memory_size = self.shortterm_memory_size,
            max_revision_cycles = self.max_revision_cycles,
            embedding_model = self.embedding_model,
            embedding_dimension = self.embedding_dimension,
            reranking_model = self.reranking_model,
            reranking_threshold = self.reranking_threshold,
        )
        self.schema_updater = SchemaUpdater(
            shortterm_memory_size = self.shortterm_memory_size,
        )

        # Add nodes
        self.builder.add_node("topic_checker", self.topic_checker)
        self.builder.add_node("message_analysis", self.message_analysis)
        self.builder.add_node("long_term_memory_retriever", self.long_term_memory_retriever)
        self.builder.add_node("agents", self.agents)
        self.builder.add_node("schema_updater", self.schema_updater)

        # Build edges
        self.builder.add_edge(START, "topic_checker")
        self.builder.add_edge("topic_checker", "message_analysis")
        self.builder.add_edge("message_analysis", "long_term_memory_retriever")
        self.builder.add_edge("long_term_memory_retriever", "agents")
        self.builder.add_edge("agents", "schema_updater")
        self.builder.add_edge("schema_updater", END)

        return self.builder

    
class Graph:
    @staticmethod
    def compile(
            chat_model: str,
            temperature: float,
            embedding_model: str,
            embedding_dimension: int,
            reranking_model: str,
            reranking_threshold: float,
            max_workers: int,
            topic_threshold: float,
            qdrant_threshold: float,
            shortterm_memory_size: int,
            max_revision_cycles: int,
        ):
        builder = GraphBuilder(
            chat_model = chat_model,
            temperature = temperature,
            embedding_model = embedding_model,
            embedding_dimension = embedding_dimension,
            reranking_model = reranking_model,
            reranking_threshold = reranking_threshold,
            max_workers = max_workers,
            topic_threshold = topic_threshold,
            qdrant_threshold = qdrant_threshold,
            shortterm_memory_size = shortterm_memory_size,
            max_revision_cycles = max_revision_cycles,
        )
        memory = MemorySaver()
        return builder.build_graph().compile(checkpointer = memory)


def build_graph(
        chat_model: str,
        temperature: float,
        embedding_model: str,
        embedding_dimension: int,
        reranking_model: str,
        reranking_threshold: float,
        max_workers: int,
        topic_threshold: float,
        qdrant_threshold: float,
        shortterm_memory_size: int,
        max_revision_cycles: int,
        save_graph: bool = False,
    ):
    graph = Graph.compile(
        chat_model = chat_model,
        temperature = temperature,
        embedding_model = embedding_model,
        embedding_dimension = embedding_dimension,
        reranking_model = reranking_model,
        reranking_threshold = reranking_threshold,
        max_workers = max_workers,
        topic_threshold = topic_threshold,
        qdrant_threshold = qdrant_threshold,
        shortterm_memory_size = shortterm_memory_size,
        max_revision_cycles = max_revision_cycles,
    )
    if save_graph:
        with open("services/chatbot/assets/chatbot-overview.png", "wb") as f:
            f.write(graph.get_graph().draw_mermaid_png())
    return graph