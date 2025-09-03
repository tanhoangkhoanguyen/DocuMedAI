from playground.khoanth.chatbot.core.workflow import build_graph

from langchain_core.messages import HumanMessage, AIMessage

def call_agent(user_input:str, chat_id:str):
    human_msg = HumanMessage(content=user_input)
    print("----------")
    init_state = {
        "chat_history": [human_msg]
    }
    result = graph.invoke(
        input=init_state,
        # config={"configurable": {"thread_id": chat_id}}
    )
    for m in result["chat_history"]:
        m.pretty_print()

def test_chatbot():
    call_agent("""
I am a tenant in New York and my landlord hasn’t fixed the broken heating for two weeks.
Could you explain my legal options?
What laws protect tenants in this situation?
I am so sad, what should I do in this situation?
Response in markdown format please. Also, think carefully before answering me.
               """, "session-123")
#     call_agent("""
# The user is a tenant living in California, worried about eviction and also owns a car without insurance. They are casually chatting with the assistant. I am so bored. Can you tell me a fun fact?
# What is the penalty for driving without insurance in California?
# Can my landlord evict me without giving proper notice?
# Provide answers in clear bullet points for each question separately. Start legal answers with 'Legal Advice:' and casual responses with 'Chat Response:'. Identify legal queries, provide accurate references to state laws, and separate casual conversation responses.
# """)
#     call_agent("""
# From now on, provide answers in clear bullet points for each question separately. Start legal answers with 'Legal Advice:' and casual responses with 'Chat Response:'. Identify legal queries, provide accurate references to state laws, and separate casual conversation responses.
# """, "session-123")

if __name__ == "__main__":
    model_name = 'gpt-4o-mini'
    global graph
    graph = build_graph(model_name = model_name)
    test_chatbot()



