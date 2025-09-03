from langgraph.graph import StateGraph, START, END
from typing import TypedDict
from IPython.display import display, Image

# Define state
class State(TypedDict):
    sequence: list[str]
    idx: int

# Define node behavior
def dispatcher(state: State) -> dict:
    # Initialize idx if not present
    return {"idx": 0}

def node_runner(state: State) -> dict:
    sequence = state["sequence"]
    idx = state["idx"]
    node_name = sequence[idx]
    print(f"Processing: {node_name}")  # Replace with actual node logic
    return {
        "idx": idx + 1
    }

def terminator(state: State) -> dict:
    print("Finished sequence")
    return {}

# Build graph
builder = StateGraph(State)
builder.add_node("dispatcher", dispatcher)
builder.add_node("node_runner", node_runner)
builder.add_node("terminator", terminator)

builder.add_edge(START, "dispatcher")
builder.add_edge("dispatcher", "node_runner")
# Continue executing node_runner until done
builder.add_conditional_edges("node_runner", lambda s: "node_runner" if s["idx"] < len(s["sequence"]) else "terminator")
builder.add_edge("terminator", END)

graph = builder.compile()

with open ("playground/khoanth/systemDesign-phase_2.png", "wb") as f:
    f.write(graph.get_graph().draw_mermaid_png())

initial_state = {"sequence": ["node_1", "node_2", "node_3", "node_1"]}
graph.invoke(initial_state)