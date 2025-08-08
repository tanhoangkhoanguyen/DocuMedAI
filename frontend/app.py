# frontend/app.py

import os
import uuid
import requests
import streamlit as st

from constants.chatbot_schemas import ChatRequest, ChatResponse

def main():
    chatbot_reply=""
    st.set_page_config(
        page_title="Law Advisory Chatbot",
        page_icon="🤖",
        layout="centered"
    )

    chatbot_service_base = os.getenv(
        "CHATBOT_SERVICE_URL",
        "http://la-backend-service:9004"
    )
    
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

    # 2. Initialize chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
    
    if prompt := st.chat_input("What's up?"):
        st.chat_message("user").markdown(prompt)
        st.session_state.messages.append({
            "role": "user",
            "content": prompt
        })

        chat_request = ChatRequest(
            session_id=st.session_state.session_id,
            message=prompt
        )
        payload = chat_request.model_dump()

        try:
            resp = requests.post(
                f"{chatbot_service_base}/chat",
                json=payload,
                timeout=30
            )
            resp.raise_for_status()
            chat_response = ChatResponse.model_validate(resp.json())
            chatbot_reply = chat_response.response
        except Exception as e:
            chatbot_reply = f"Error: {e}"
        
        with st.chat_message("assistant"):
            st.markdown(chatbot_reply)
        
        st.session_state.messages.append({
            "role": "assistant",
            "content": chatbot_reply
        })

if __name__ == "__main__":
    main()
    