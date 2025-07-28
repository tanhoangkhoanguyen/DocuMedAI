from langgraph.graph import StateGraph


from backend.services.agents.src.state_schema import State
from .resolved_node_functions import *


def build_resolved_graph():
    builder = StateGraph(State)
    builder.add_node(resolved_intent_detector)
    law_advisory = LawAdvisory()
    builder.add_node("law_advisory", law_advisory)
    builder.add_node(other)

    builder.set_entry_point("resolved_intent_detector")
    builder.add_conditional_edges(
            "resolved_intent_detector",
            resolved_decide_node,
            ["law_advisory", "other"]
        )
    builder.set_finish_point("law_advisory")
    builder.set_finish_point("other")

    return builder