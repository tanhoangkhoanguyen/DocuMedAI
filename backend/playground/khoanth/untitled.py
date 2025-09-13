import warnings
warnings.filterwarnings("ignore")

from dotenv import load_dotenv, find_dotenv
from langchain_openai import ChatOpenAI

_ = load_dotenv(find_dotenv())
chat = ChatOpenAI(model = 'gpt-3.5-turbo', temperature = 0)

# from typing import TypedDict
# from typing_extensions import Annotated
# from langchain_core.messages import AnyMessage, SystemMessage, HumanMessage, RemoveMessage
# from langgraph.graph.message import add_messages
# from langchain_core.runnables import RunnableConfig
# from langgraph.graph import StateGraph, END
# from langgraph.checkpoint.memory import MemorySaver
# from IPython.display import display, Image

# class State(TypedDict):
#     messages: Annotated[list[AnyMessage], add_messages]
#     summary: str


# def call_model(state: State, config: RunnableConfig):
#     summary = state.get("summary", "")

#     if summary:
#         system_message = f"Summary of conversation earlier: {summary}"
#         messages = [SystemMessage(content = system_message)] + state["messages"]
#     else:
#         messages = state["messages"]
    
#     # Without this LangChain assumes stream = False
#     response = chat.invoke(messages, config)
#     return {"messages": response}

# def summarize_conversation(state: State):
#     summary = state.get("summary", "")
    
#     if summary:
#         summary_message = (
#             f"This is summary of the conversation to date: {summary}\n\n"
#             "Extend the summary by taking into account the new messages above:"
#         )
#     else:
#         summary_message = "Create a summary of the conversation above:"

#     messages = state["messages"] + [HumanMessage(content = summary_message)]
#     response = chat.invoke(messages)
    
#     delete_messages = [RemoveMessage(id = m.id) for m in state["messages"][:-2]]
#     return {"summary": response.content, "messages": delete_messages}

# def should_continue(state: State):
#     messages = state["messages"]
    
#     if len(messages) > 6:
#         return "summarize_conversation"
#     return END


# builder = StateGraph(State)
# builder.add_node(call_model)
# builder.add_node(summarize_conversation)

# builder.set_entry_point("call_model")
# builder.add_conditional_edges(
#     "call_model", 
#     should_continue, {
#         END: END,
#         "summarize_conversation": "summarize_conversation"
# })
# builder.add_edge("summarize_conversation", END)


# memory = MemorySaver()
# graph = builder.compile(checkpointer = memory)
# display(Image(graph.get_graph().draw_mermaid_png()))

# streaming_node = 'call_model'
# config = {"configurable": {"thread_id": "4"}}

# import asyncio

# async def main():
#     streaming_node = 'call_model'
#     config = {"configurable": {"thread_id": "4"}}

#     async for event in graph.astream_events(
#         {"messages": [HumanMessage(content="Tell me about Squid Game 3")]},
#         config,
#         version="v2"
#     ):
#         if event["event"] == "on_chat_model_stream" and event['metadata'].get('langgraph_node','') == streaming_node:
#             print(event["data"])

# # Run the async code
# asyncio.run(main())

print ("Hi, it is running")
Final = ""
for chunk in chat.stream("what is the meaning of family?"):
    print(chunk.content, end="", flush=True)
    Final += chunk.content

print ('\n' * 3, Final)