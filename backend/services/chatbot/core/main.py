from services.chatbot.core.workflow import build_graph

import os, sys
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
    print(f"""
        [INFO] [backend.services.chatbot.core.main] AI response:
        \t{chatbot_response}
    """)

def test_chatbot():
    call_agent("""
        Hi chatbot.
    """)

if __name__ == "__main__":
    user_input = input("Type 'Execute' to run: ")
    if user_input != "Execute":
        sys.exit()

    # Fast and low cost, with low intelligence large language model.
    # Reference: https://platform.openai.com/docs/models/compare
    chat_model = "gpt-4o-mini"                                 

    # Fast, medium quality embedding model.
    embedding_model = "sentence-transformers/all-MiniLM-L6-v2"

    # cross-encoder reranking model
    reranking_model = "BAAI/bge-reranker-v2-m3"

    # Thresholds are task-dependent. For custom definitions of similarity or domain-specific embeddings, thresholds must 
    # be tuned empirically, or if labeled data is unavailable, assumptions based on embedding distributions are acceptable.
    qdrant_threshold = 0.25
    reranking_threshold = -5

    chat_id = "session-123"

    global graph
    graph = build_graph(
        chat_model = chat_model,
        embedding_model = embedding_model,
        reranking_model = reranking_model,
        qdrant_threshold = qdrant_threshold,
        reranking_threshold = reranking_threshold,
        chat_id = chat_id
    )
    test_chatbot()