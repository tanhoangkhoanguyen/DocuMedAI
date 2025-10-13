from playground.khoanth.chatbot.core.constants.schemas import GraphState
from playground.khoanth.chatbot.core.agents.general import MessageAnalysis, NodeController, SchemaResetNode

from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

class GraphBuilder:
    def __init__(
            self, 
            chat_model:str, 
            embedding_model:str,
            max_workers:int
        ):
        self.builder = StateGraph(GraphState)
        self.chat_model = chat_model
        self.embedding_model = embedding_model
        self.max_workers = max_workers

    def build_graph(self):
        self.message_analysis = MessageAnalysis(
            chat_model = self.chat_model
        )
        self.node_controller = NodeController(
            chat_model = self.chat_model,
            embedding_model = self.embedding_model,
            max_workers = self.max_workers
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
            chat_model:str, 
            embedding_model:str,
            max_workers:int
        ):
        builder = GraphBuilder(
            chat_model = chat_model,
            embedding_model = embedding_model,
            max_workers = max_workers
        )
        memory = MemorySaver()
        return builder.build_graph().compile(checkpointer = memory)

def build_graph(
        chat_model:str, 
        embedding_model:str,
        max_workers:str,
        save_graph:bool = False
    ):
    graph = Graph.compile(
        chat_model = chat_model,
        embedding_model = embedding_model,
        max_workers = max_workers
    )
    if save_graph:
        with open("playground/khoanth/chatbot/assets/chatbot-phase_2.png", "wb") as f:
            f.write(graph.get_graph().draw_mermaid_png())
    return graph



