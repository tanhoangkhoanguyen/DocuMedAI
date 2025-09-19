from services.chatbot.core.workflow import build_graph
from services.chatbot.core.main import call_agent
from services.chatbot.core.constants.schemas import LawAgentState

from langchain_core.messages import HumanMessage, AIMessage


def test_route():
    call_agent("Hello, my name is Phuc.") # Greeting
    # call_agent("Your response is not as what I have expected.") # Complaint request
    # call_agent("I want the responss to be short and in the markdown format.") # Instruction 
    # call_agent("If I stole 500$, how long will I be sentenced?") # Law
    # call_agent("I want to ask about the US law") # Law
    # call_agent("Hi, what's the weather today?") # Chit chat


if __name__ == "__main__":
    model_name = 'gpt-4o-mini'
    global graph 
    graph = build_graph(model_name = model_name)

    test_route()




