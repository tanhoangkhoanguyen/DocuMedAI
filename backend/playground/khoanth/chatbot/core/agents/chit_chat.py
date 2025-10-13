from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI

class ChitChater:
    def __init__(self, chat_model:str, temperature:int = 0):
        self.__llm = ChatOpenAI(model = chat_model, temperature = temperature)

    def executor(self, user_message, config = None):
        response = self.__llm.invoke(user_message)
        return response.content