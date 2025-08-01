from services.chatbot.core.constants.schemas import LawAgentState

from services.chatbot.core.agents.general import IntentDetector, Greetor, InstructionSupporter, ChitChater, ComplaintSupporter
from services.chatbot.core.agents.law_advisory import LawAdvisor

from dotenv import load_dotenv
load_dotenv()

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

class GraphBuilder:
    def __init__(self, model_name:str):
        self.builder = StateGraph(LawAgentState)
        self.model_name = model_name
    
    def route_by_topic(self, state:LawAgentState):
        topic_id = state.topic_id
        print(f"Route by topic: {topic_id}")
        if topic_id==0:
            return "greeting"
        elif topic_id==1:
            return "complaint_request"
        elif topic_id==2:
            return "instruction_support"
        elif topic_id==3:
            return "law_support"
        else:
            return "chit_chat"
    
    def build_graph(self):
        self.intent_detector = IntentDetector(model_name=self.model_name)
        self.greetor = Greetor(model_name=self.model_name)
        self.instruction_supporter = InstructionSupporter(model_name=self.model_name)
        self.chit_chater = ChitChater(model_name=self.model_name)
        self.law_advisor = LawAdvisor(model_name=self.model_name)
        self.complaint_supporter = ComplaintSupporter(model_name=self.model_name)

        self.builder.add_node("intent_detector", self.intent_detector)
        self.builder.add_node("greeting", self.greetor)
        self.builder.add_node("complaint_request", self.complaint_supporter)
        self.builder.add_node("instruction_support", self.instruction_supporter)
        self.builder.add_node("chit_chat", self.chit_chater)
        self.builder.add_node("law_support", self.law_advisor)

        self.builder.add_edge(START, "intent_detector")
        self.builder.add_conditional_edges("intent_detector",
                                           self.route_by_topic,
                                           {
                                               "greeting": "greeting",
                                               "complaint_request": "complaint_request",
                                               "instruction_support": "instruction_support",
                                               "chit_chat": "chit_chat",
                                               "law_support": "law_support"
                                           })

        self.builder.add_edge("greeting", END)
        self.builder.add_edge("complaint_request", END)
        self.builder.add_edge("instruction_support", END)
        self.builder.add_edge("chit_chat", END)
        self.builder.add_edge("law_support", END)

        return self.builder
    
class Graph:
    @staticmethod
    def compile(model_name:str):
        builder = GraphBuilder(model_name=model_name)
        memory = MemorySaver()
        return builder.build_graph().compile(checkpointer=memory)

def build_graph(model_name:str, save_graph:bool=True):
    graph = Graph.compile(model_name=model_name)
    if save_graph:
        with open("services/chatbot/assets/graph.png", "wb") as f:
            f.write(graph.get_graph().draw_mermaid_png())
            print("Graph image is saved to ervices/chatbot/assets/graph.png")
    return graph
