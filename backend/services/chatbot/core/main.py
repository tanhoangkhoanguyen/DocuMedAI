from services.chatbot.core.workflow import build_graph

from langchain_core.messages import HumanMessage, AIMessage

def call_agent(user_input:str):
    human_msg = HumanMessage(content=user_input)
    print("----------")
    init_state = {
        "messages": [human_msg]
    }
    result = graph.invoke(
        input=init_state,
        config={"configurable": {"thread_id": "session-123"}}
    )
    
    ai_response = result["messages"][-1]
    chatbot_response = ai_response if isinstance(ai_response, AIMessage) else "Sorry, I didn't understand that."
    print(f"AI Response: {chatbot_response}")
    print(f"Topic id: {result['topic_id']}")

if __name__ == "__main__":
    model_name = 'gpt-4o-mini'
    global graph 
    graph = build_graph(model_name = model_name)




