from langchain_core.messages import HumanMessage, SystemMessage


from backend.src.workflow import build_graph


def run_graph(message):
    graph = build_graph().compile()    
    response = graph.invoke({'messages': [HumanMessage(content = message)]})
    bot_reply = response['messages'][-1].content
    return bot_reply


if __name__ == "__main__":
    graph = build_graph().compile()
    with open("backend/workflow.png", "wb") as f:
        f.write(graph.get_graph(xray = True).draw_mermaid_png())