from playground.khoanth.chatbot.core.workflow import build_graph

import os
from langchain_core.messages import HumanMessage, AIMessage

def call_agent(user_input:str, chat_id:str):
    human_msg = HumanMessage(content = user_input)
    init_state = {
        "chat_history": [human_msg]   
    }
    result = graph.invoke(
        input = init_state,
        config = {"configurable": {"thread_id": chat_id}}
    )
    ai_response = result["chat_history"][-1]
    chatbot_response = ai_response.content if isinstance(ai_response, AIMessage) else "Sorry, I didn't understand that."
    print (chatbot_response)

def test_chatbot():
    call_agent("""
            I am a tenant in New York and my landlord hasn’t fixed the broken heating for two weeks.
            What laws protect tenants in this situation?
            Response in markdown format please. Also, think carefully before answering me.
        """, # What is x, given x + 9 = 210? 
             # I am so sad, what should I do in this situation?
        "123456"
    )
    # call_agent("""
    #         Respond your previous reply in 1 super short setence.
    #     """, 
    #     "123456"
    # )
    # call_agent("""
    #         Chatbot response is too dump. From now on, reponse in 1 paragraph ok?
    #     """, 
    #     "123456"
    # )
    # call_agent("""
    #         Teach me everything I need to know to learn Python.
    #     """, 
    #     "123456"
    # )
    # call_agent("""
    #         What is my case with the landlord?
    #         How can I use Python to build tools that help with cases like me?
    #     """, 
    #     "123456"
    # )

if __name__ == "__main__":
    chat_model = "gpt-4o-mini"
    embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
    max_workers = max(1, os.cpu_count() - 3)
    global graph
    graph = build_graph(
        chat_model = chat_model,
        embedding_model = embedding_model,
        max_workers = max_workers
    )
    test_chatbot()



