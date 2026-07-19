"""
RAG-only prompt templates, used exclusively by utils/rag.py.

Lives beside rag.py in backend/utils so the shared RAG layer does not import back
into services.chatbot.constants.prompts (which holds chatbot-graph prompts).
"""

PARAPHRASE_MESSAGE_PROMPT = """
You are an expert in message rephrasing.
Generate {number} versions of the user’s message that keep the original meaning but express it in different ways to improve search coverage.
"""

GENERALIZE_USER_MESSAGE_PROMPT = """
Paraphrase the message into a more general, easier-to-answer version.
EXAMPLE

Input: "Could the members of The Police perform lawful arrests?"
Output: "What can the members of The Police do?"

Input: "Lionel Messi's was born in what country?"
Output: "What is Lionel Messi's personal history?"
"""
