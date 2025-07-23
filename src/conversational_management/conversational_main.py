from langchain_core.messages import HumanMessage


from .conversational_workflow import build_conversational_graph


if __name__ == "__main__":
    graph = build_conversational_graph().compile()
    with open("src/conversational_management/conversational_workflow.png", "wb") as f:
        f.write(graph.get_graph().draw_mermaid_png())
        
    # message = [
    #     HumanMessage(content = "Contact the developer")
    # ]
    # response = graph.invoke({"messages": message})