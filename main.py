from langchain_core.messages import HumanMessage


from src.workflow import build_graph

if __name__ == "__main__":
    graph = build_graph().compile()
    with open("workflow.png", "wb") as f:
        f.write(graph.get_graph(xray = True).draw_mermaid_png())
        
    # message = [
    #     HumanMessage(content = "How are you?")
    # ]
    # response = graph.invoke({"messages": message})