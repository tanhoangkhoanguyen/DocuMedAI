from langchain_core.messages import HumanMessage


from .resolved_workflow import build_resolved_graph


if __name__ == "__main__":
    graph = build_resolved_graph().compile()
    with open("backend/services/agents/image/resolved_workflow.png", "wb") as f:
        f.write(graph.get_graph().draw_mermaid_png())
    
    message = [
        HumanMessage(content = "How long will I be sentenced if I broke into a house?")
    ]
    response = graph.invoke({"messages": message})
    print (response)