from services.chatbot.core.constants.schemas import TopicIDResponse
from services.chatbot.core.constants.prompts import PARAPHRASE_USER_MESSAGE_PROMPT, GENERALIZE_USER_MESSAGE_PROMPT, LAW_CLASSIFIER_PROMPT

from dotenv import load_dotenv
load_dotenv()
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from langchain_openai import ChatOpenAI
from langsmith import traceable
from langchain.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate
from langchain_core.output_parsers import StrOutputParser

class UserMessagePreprocesser:
    def __init__(self, llm_model="gpt-4o-mini"):
        self.__llm = ChatOpenAI(model=llm_model, temperature=0)
        self.__paraphrase_user_message_prompt = ChatPromptTemplate.from_template(PARAPHRASE_USER_MESSAGE_PROMPT)

        examples = [
            {
                "input": "Could the members of The Police perform lawful arrests?",
                "output": "what can the members of The Police do?",
            },
            {
                "input": "Lionel Messi's was born in what country?",
                "output": "what is Lionel Messi's personal history?",
            }
        ]
        example_prompt = ChatPromptTemplate.from_messages([
            ("human", "{input}"),
            ("ai", "{output}"),
        ])
        few_shot_prompt = FewShotChatMessagePromptTemplate(
            example_prompt = example_prompt,
            examples = examples,
        )
        self.__generalize_user_message_prompt = ChatPromptTemplate.from_messages([
            ("system", GENERALIZE_USER_MESSAGE_PROMPT),
            few_shot_prompt,
            ("user", "{query}"),
        ])
    
    @traceable
    def __paraphrase_user_message(self, message, number=3):
        queries = (
            self.__paraphrase_user_message_prompt 
            | self.__llm
            | StrOutputParser() 
            | (lambda x: x.split("\n"))
        ).invoke({
            "number": number,
            "query": message
        })
        return [doc for doc in queries if doc != '']

    @traceable
    def __generalize_user_message(self, message):
        query = (
            self.__generalize_user_message_prompt 
            | self.__llm
        ).invoke({"query": message})
        return query.content
    
    def rephrase_user_message(self, message: str, number:int = 3, timeout:float | None = None):
        with ThreadPoolExecutor(max_workers=2) as pool:
            print(f"[INFO] Multithreading for processing user message")
            fut_para = pool.submit(self.__paraphrase_user_message, message, number)
            fut_gener  = pool.submit(self.__generalize_user_message, message)

            try:
                para_query_resp = fut_para.result(timeout=timeout)
            except TimeoutError:
                print(f"[TIMEOUT ERROR] From threading for paraphrasing user message: {str(e)}")
                print(f"[ERROR] Canceled paraphrasing user message.")
                fut_para.cancel()
            except Exception as e:
                print(f"[ERROR] From threading for paraphrasing user message: {str(e)}")

            try:
                gener_query_resp = fut_gener.result(timeout=timeout)
            except TimeoutError:
                print(f"[TIMEOUT ERROR] From threading for generalizing user message: {str(e)}")
                print(f"[ERROR] Canceled generalizing user message.")
                fut_gener.cancel()
            except Exception:
                print(f"[ERROR] From threading for generalizing user message: {str(e)}")

        return para_query_resp + [gener_query_resp]


class LawTypeIdentifier:
    def __init__(self, llm_model="gpt-4o-mini"):
        self.__structured_llm = ChatOpenAI(model=llm_model, temperature=0).with_structured_output(TopicIDResponse)
        self.__support_law_type = ["civil_law", "criminal_law", "environmental_law", "international_law", "labor_and_employment_law"]
    
    def identify_law_type_from_user_message(self, user_message):
        prompt = LAW_CLASSIFIER_PROMPT.format(message=user_message)
        response = self.__structured_llm.invoke(prompt)
        id = response.id
        return self.__support_law_type[id]
    
