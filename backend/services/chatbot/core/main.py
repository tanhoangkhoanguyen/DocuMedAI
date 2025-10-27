from services.chatbot.core.workflow import build_graph

import os
from langchain_core.messages import HumanMessage, AIMessage

def call_agent(user_input:str, chat_id:str = "session-123"):
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
    print(f"""
        [INFO] [backend.services.chatbot.core.main] AI response:
        \t{chatbot_response}
    """)

def test_chatbot():
    call_agent("""
        Hi chatbot.
    """)
    return
    call_agent("""
            I am a tenant in New York and my landlord hasn’t fixed the broken heating for two weeks.
            What laws protect tenants in this situation?
            Response in markdown format please. Also, think carefully before answering me.
        """)
    call_agent("""
            Rewrited your previous response in 1 super short setence.
        """)
    call_agent("""
            Chatbot response is too dump. From now on, reponse in 1 paragraph ok?
        """)
    call_agent("""
            In strictly 50 words, teach me everything I need to know to learn Python.
        """)
    call_agent("""
            What is my case with the landlord?
            How can I use Python to build tools that help with cases like me?
        """)

if __name__ == "__main__":
    chat_model = "gpt-4o-mini"
    embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
    qdrant_threshold = 0.25
    reranking_model = "BAAI/bge-reranker-v2-m3"
    reranking_threshold = -5
    max_workers = max(1, os.cpu_count() - 3)
    global graph
    graph = build_graph(
        chat_model = chat_model,
        embedding_model = embedding_model,
        qdrant_threshold = qdrant_threshold,
        reranking_model = reranking_model,
        reranking_threshold = reranking_threshold,
        max_workers = max_workers
    )
    test_chatbot()