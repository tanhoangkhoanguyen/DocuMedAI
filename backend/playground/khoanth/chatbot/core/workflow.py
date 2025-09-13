from playground.khoanth.chatbot.core.constants.schemas import GraphState
from playground.khoanth.chatbot.core.agents.general import MessageAnalysis, NodeController, SchemaResetNode

from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

class GraphBuilder:
    def __init__(self, model_name:str):
        self.builder = StateGraph(GraphState)
        self.model_name = model_name

    def build_graph(self):
        self.message_analysis = MessageAnalysis(model_name=self.model_name)
        self.node_controller = NodeController(model_name=self.model_name)
        self.schema_reset_node = SchemaResetNode(model_name=self.model_name)

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
    def compile(model_name:str):
        builder = GraphBuilder(model_name=model_name)
        memory = MemorySaver()
        return builder.build_graph().compile(checkpointer = memory)

def build_graph(model_name:str, save_graph:bool=True):
    graph = Graph.compile(model_name=model_name)
    if save_graph:
        with open("playground/khoanth/chatbot/assets/chatbot-phase_2.png", "wb") as f:
            f.write(graph.get_graph().draw_mermaid_png())
            print("Graph image is saved to playground/khoanth/chatbot/assets/graph.png")
    return graph



