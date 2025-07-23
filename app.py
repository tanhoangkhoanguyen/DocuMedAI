import streamlit as st
import uuid
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage


from src.workflow import build_graph


if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

memory = MemorySaver()
graph = build_graph().compile(checkpointer = memory)

# Side-bar usage guide
with st.sidebar:
    st.markdown(""" ## 👋 Welcome to the Law Advisory Chatbot
This chatbot is designed to help you explore and understand legal topics with ease. You can ask questions and receive responses in a conversational format.

### How to Use:
- Enter your message in the input box.
- Click the `Execute` button to send your message.
- Your conversation history will appear in the chat window, allowing you to track previous interactions.

### Important Notes:         
For more accurate and relevant responses, please avoid combining multiple questions or topics in a single message.
    """)


# Chat interface
st.title("📚 Law Advisory chatbot")
input_message = st.text_input("Enter your base message (e.g., 'Hello.')")

if st.button("Execute ✨"):
    if input_message.strip() == "":
        st.warning("Please enter a message.")
    else:
        st.session_state.chat_history.append(("user", input_message))

        key = uuid.uuid4()
        config = {"configurable": {"thread_id": str(key)}}
        messages = [
            HumanMessage(content = input_message)
        ]
        response = graph.invoke({"messages": messages}, config)
        bot_reply = response['messages'][-1].content

        st.session_state.chat_history.append(("bot", bot_reply))


# Display chat history
st.markdown("---")
st.markdown("### 🕘 Chat History")

length = len(st.session_state.chat_history)
for i in range(length - 1, -1, -1):
    role, msg = st.session_state.chat_history[i]
    if role == "user":
        st.markdown(f"🤔 {msg}")
    else:
        st.markdown(f"🤖 {msg}")