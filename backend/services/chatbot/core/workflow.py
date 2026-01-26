from services.chatbot.core.constants.schemas import GraphState
from services.chatbot.core.agents.general import MessageAnalysis, NodeController, SchemaResetNode

from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

class GraphBuilder:
    def __init__(
            self,
            chat_model: str, 
            embedding_model: str,
            reranking_model: str,
            qdrant_threshold: int,
            reranking_threshold: int
        ):
        self.builder = StateGraph(GraphState)
        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.reranking_model = reranking_model
        self.qdrant_threshold = qdrant_threshold
        self.reranking_threshold = reranking_threshold

    def build_graph(self):
        self.message_analysis = MessageAnalysis(
            chat_model = self.chat_model
        )
        self.node_controller = NodeController(
            chat_model = self.chat_model,
            embedding_model = self.embedding_model,
            reranking_model = self.reranking_model,
            qdrant_threshold = self.qdrant_threshold,
            reranking_threshold = self.reranking_threshold,
        )
        self.schema_reset_node = SchemaResetNode(
            chat_model = self.chat_model,
            embedding_model = self.embedding_model
        )

        self.builder.add_node("message_analysis", self.message_analysis)
        self.builder.add_node("node_controller", self.node_controller)
        self.builder.add_node("schema_reset_node", self.schema_reset_node)

        self.builder.add_edge(START, "message_analysis")
        self.builder.add_edge("message_analysis", "node_controller")
        self.builder.add_edge("node_controller", "schema_reset_node")
        self.builder.add_edge("schema_reset_node", END)

        return self.builder
    
class Graph:
    @staticmethod
    def compile(
            chat_model: str, 
            embedding_model: str,
            reranking_model: str,
            qdrant_threshold: int,
            reranking_threshold: int
        ):
        builder = GraphBuilder(
            chat_model = chat_model,
            embedding_model = embedding_model,
            reranking_model = reranking_model,
            qdrant_threshold = qdrant_threshold,
            reranking_threshold = reranking_threshold
        )
        memory = MemorySaver()
        return builder.build_graph().compile(checkpointer = memory)

def build_graph(
        chat_model: str, 
        embedding_model: str,
        reranking_model: str,
        qdrant_threshold: int,
        reranking_threshold: int,
        save_graph: bool = False
    ):
    graph = Graph.compile(
        chat_model = chat_model,
        embedding_model = embedding_model,
        reranking_model = reranking_model,
        qdrant_threshold = qdrant_threshold,
        reranking_threshold = reranking_threshold
    )
    if save_graph:
        with open("services/chatbot/assets/chatbot-phase_2.png", "wb") as f:
            f.write(graph.get_graph().draw_mermaid_png())
    return graph