from services.chatbot.constants.schemas import GraphState
from services.chatbot.nodes import MessageAnalysis, LongTermMemoryRetriever, NodeController, SchemaResetNode

from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver


class GraphBuilder:
    def __init__(
            self,
        ):
        self.builder = StateGraph(GraphState)

    def build_graph(self):
        self.message_analysis = MessageAnalysis(
        )
        self.long_term_memory_retriever = LongTermMemoryRetriever(
        )
        self.node_controller = NodeController(
        )
        self.schema_reset_node = SchemaResetNode(
        )

        # Add nodes
        self.builder.add_node("message_analysis", self.message_analysis)
        self.builder.add_node("long_term_memory_retriever", self.long_term_memory_retriever)
        self.builder.add_node("node_controller", self.node_controller)

        # Build edges
        self.builder.add_edge(START, "message_analysis")
        self.builder.add_edge("message_analysis", "long_term_memory_retriever")
        self.builder.add_edge("long_term_memory_retriever", "node_controller")
        self.builder.add_edge("node_controller", END)

        return self.builder

    
class Graph:
    @staticmethod
    def compile(
        ):
        builder = GraphBuilder(
        )
        memory = MemorySaver()
        return builder.build_graph().compile(checkpointer = memory)


def build_graph(
        save_graph: bool = False
    ):
    graph = Graph.compile(
    )
    if save_graph:
        with open("services/chatbot/assets/chatbot-overview.png", "wb") as f:
            f.write(graph.get_graph().draw_mermaid_png())
    return graph