from langgraph.graph import StateGraph


from .state_schema import State
from .node_functions import *
from .conversational_management.conversational_workflow import build_conversational_graph
from .problem_resolution.resolved_workflow import build_resolved_graph


def build_graph():
    builder = StateGraph(State)
    builder.add_node(intent_detector)
    builder.add_node("conversational_management", build_conversational_graph().compile())

    builder.set_entry_point("intent_detector")
    builder.add_edge("intent_detector", "conversational_management")
    builder.set_finish_point("conversational_management")

    return builder