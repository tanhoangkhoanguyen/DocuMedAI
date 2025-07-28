from langgraph.graph import StateGraph


from backend.services.agents.src.state_schema import State
from .conversational_node_functions import *


def build_conversational_graph():
    builder = StateGraph(State)
    builder.add_node(conversational_intent_detector)
    builder.add_node(greeting)
    builder.add_node(add_instruction)
    builder.add_node(complain_contact)

    builder.set_entry_point("conversational_intent_detector")
    builder.add_conditional_edges(
            "conversational_intent_detector",
            conversational_decide_note,
            ["greeting", "add_instruction", "complain_contact"]
        )
    builder.set_finish_point("greeting")
    builder.set_finish_point("add_instruction")
    builder.set_finish_point("complain_contact")

    return builder