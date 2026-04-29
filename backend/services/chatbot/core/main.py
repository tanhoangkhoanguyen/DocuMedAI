from services.chatbot.core.workflow import build_graph
from services.chatbot.core.constants.schemas import UnitState
from logger import get_logger

from langchain_core.messages import HumanMessage, AIMessage

_LOGGER = get_logger(
    name = "chatbot",
    level = "INFO"
)

def call_agent(user_input: str, chat_id: str):
    human_msg = HumanMessage(content = user_input)
    init_state = UnitState(
        chat_history = [human_msg]   
    )
    try:
        result = graph.invoke(
            input = init_state,
            config = {"configurable": {"thread_id": chat_id}}
        )

        if not result.get("chat_history", []):
            raise ValueError("Empty chat history")

        ai_response = result["chat_history"][-1]
        if isinstance(ai_response, AIMessage):
            chatbot_response = ai_response.content
        else:
            chatbot_response = "Sorry, I didn't understand that."
        _LOGGER.info(f"Chatbot response: {chatbot_response}")
    except Exception as e:
        _LOGGER.error(f"Error during graph invoke: {e}")

def test_chatbot():
    call_agent("""
        Hi chatbot.
    """, chat_id = "12345")

if __name__ == "__main__":
    # Fast and low cost, with low intelligence large language model.
    # Reference: https://platform.openai.com/docs/models/compare
    chat_model = "gpt-4o-mini"
    chat_model = "gemini-2.5-flash"

    # Fast, medium quality embedding model.
    embedding_model = "sentence-transformers/all-MiniLM-L6-v2"

    # cross-encoder reranking model
    reranking_model = "BAAI/bge-reranker-v2-m3"

    # Thresholds are task-dependent. For custom definitions of similarity or domain-specific embeddings, thresholds must 
    # be tuned empirically, or if labeled data is unavailable, assumptions based on embedding distributions are acceptable.
    qdrant_threshold = 0.25
    topic_threshold = -5
    rag_threshold = -5

    chat_id = "session-123"

    global graph
    graph = build_graph(
        chat_model = chat_model,
        embedding_model = embedding_model,
        reranking_model = reranking_model,
        qdrant_threshold = qdrant_threshold,
        topic_threshold = topic_threshold,
        rag_threshold = rag_threshold,
        chat_id = chat_id
    )
    test_chatbot()