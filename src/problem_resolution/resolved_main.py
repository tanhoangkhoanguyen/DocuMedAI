from langchain_core.messages import HumanMessage


from .resolved_workflow import build_resolved_graph


if __name__ == "__main__":
    graph = build_resolved_graph().compile()
    with open("src/problem_resolution/resolved_workflow.png", "wb") as f:
        f.write(graph.get_graph().draw_mermaid_png())
    
    # message = [
    #     HumanMessage(content = "I am bullied at my school. What can I do?")
    # ]
    # response = graph.invoke({"messages": message})