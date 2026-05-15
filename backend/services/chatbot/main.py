from langchain_core.messages import HumanMessage, AIMessage

import os, json, warnings
warnings.filterwarnings("ignore")

from services.chatbot.workflow import build_graph
from services.chatbot.constants.schemas import GraphState
from vector_database_tests.utils.qdrant_client import QdrantClient
from logger import get_logger


_LOGGER = get_logger(
    name = "chatbot",
    level = "INFO"
)


def call_agent(user_input: str, chat_id: str):
    human_msg = HumanMessage(content = user_input)
    init_state = GraphState(chat_history = [human_msg])
    try:
        result = graph.invoke(
            input = init_state,
            config = {"configurable": {"thread_id": chat_id}}
        )

        if not result.get("chat_history"):
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
    # chat_model = "gpt-4o-mini"
    chat_model = "gemini-2.5-flash"
    temperature = 0

    # Fast, medium quality embedding model.
    embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dimension = 384

    # cross-encoder reranking model
    reranking_model = "BAAI/bge-reranker-v2-m3"

    if (os.cpu_count() < 8):
        _LOGGER.warning("Reconfig the max_workers variable before execution")
        raise
    max_workers = 4

    # Thresholds are task-dependent. For custom definitions of similarity or domain-specific embeddings, thresholds must 
    # be tuned empirically, or if labeled data is unavailable, assumptions based on embedding distributions are acceptable.
    topic_threshold = -5
    qdrant_threshold = 0.25
    rag_threshold = -5

    chat_id = "session-123"

    # Pipeline setup
    qdrant_client = QdrantClient(
        embedding_model = embedding_model,
        embedding_dimension = embedding_dimension
    )
    qdrant_client.create_collection("LongtermMemory")
    # Upload medical dataset
    try:
        qdrant_client.create_collection("MedicalTerms")
        batch_size = 1000
        ids, queries, embedded_queries = [], [], []
        total_indexing_time = 0

        with open("vector_database_tests/dataset/gamino-wiki_medical_terms-1.jsonl", 'r', encoding = "utf-8") as f:
            for obj in f:
                object = json.loads(obj)
                ids.append(object["id"])
                queries.append(object["split_text"])
                embedded_queries.append(object["embedded_test"])

                if len(ids) == batch_size:
                    qdrant_client.push_documents(
                        "MedicalTerms", ids, queries, embedded_queries
                    )
                    ids, queries, embedded_queries = [], [], []
            if ids:
                qdrant_client.push_documents(
                    "MedicalTerms", ids, queries, embedded_queries
                )
                ids, queries, embedded_queries = [], [], []
    except Exception as e:
        _LOGGER.error(f"Failed to upload dataset for Qdrant\n\t{str(e)}")
        raise

    shortterm_memory_size = 5
    max_revision_cycles = 3

    global graph
    graph = build_graph(
        chat_model = chat_model,
        temperature = temperature,
        embedding_model = embedding_model,
        embedding_dimension = embedding_dimension,
        reranking_model = reranking_model,
        max_workers = max_workers,
        topic_threshold = topic_threshold,
        qdrant_threshold = qdrant_threshold,
        rag_threshold = rag_threshold,
        shortterm_memory_size = shortterm_memory_size,
        max_revision_cycles = max_revision_cycles,
    )
    test_chatbot()