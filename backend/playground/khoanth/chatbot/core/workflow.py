from playground.khoanth.chatbot.core.constants.schemas import GlobalState, LocalState
from playground.khoanth.chatbot.core.agents.general import MessageAnalysis, NodeController, Synthesis
from playground.khoanth.chatbot.core.agents.chit_chat import ChitChater
from playground.khoanth.chatbot.core.agents.law_support import lawSupport

from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

class GraphBuilder:
    def __init__(self, model_name:str):
        self.builder = StateGraph(LocalState)
        self.model_name = model_name

    def route_by_topic(self, state:LocalState):
        if state.messages_idx == len(state.topic_id):
            return "systhesis"
        return "law_support" if state.topic_id[state.messages_idx] < 1 else "chit_chat"
    
    def build_graph(self):
        self.message_analysis = MessageAnalysis(model_name=self.model_name)
        self.node_controller = NodeController()
        self.synthesis = Synthesis()
        self.law_support = lawSupport(model_name=self.model_name)
        self.chit_chater = ChitChater(model_name=self.model_name)

        self.builder.add_node("message_analysis", self.message_analysis)
        self.builder.add_node("node_controller", self.node_controller)
        self.builder.add_node("law_support", self.law_support)
        self.builder.add_node("chit_chat", self.chit_chater)
        self.builder.add_node("synthesis", self.synthesis)

        self.builder.add_edge(START, "message_analysis")
        self.builder.add_edge("message_analysis", "node_controller")
        self.builder.add_conditional_edges("node_controller",
                                           self.route_by_topic,
                                           ["law_support", "chit_chat", "synthesis"])
        self.builder.add_edge("law_support", "node_controller")
        self.builder.add_edge("chit_chat", "node_controller")
        self.builder.add_edge("synthesis", END)

        return self.builder
    
class Graph:
    @staticmethod
    def compile(model_name:str):
        builder = GraphBuilder(model_name=model_name)
        return builder.build_graph().compile()

def build_graph(model_name:str, save_graph:bool=True):
    graph = Graph.compile(model_name=model_name)
    if save_graph:
        with open("playground/khoanth/chatbot/assets/chatbot-phase_2.png", "wb") as f:
            f.write(graph.get_graph().draw_mermaid_png())
            print("Graph image is saved to playground/khoanth/chatbot/assets/graph.png")
    return graph
