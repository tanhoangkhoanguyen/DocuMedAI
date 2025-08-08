from services.chatbot.core.constants.schemas import TopicIDResponse
from services.chatbot.core.constants.prompts import MULTI_QUERY_PROMPT, STEP_BACK_PROMPT, LAW_CLASSIFIER_PROMPT

import asyncio
from dotenv import load_dotenv
load_dotenv()
from langchain_openai import ChatOpenAI
from langsmith import traceable
from langchain.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate
from langchain_core.output_parsers import StrOutputParser

class UserMessagePreprocesser:
    def __init__(self, llm_model="gpt-4o-mini"):
        self.__llm = ChatOpenAI(model=llm_model, temperature=0)
    
    @traceable
    async def __paraphrase_user_message(self, message, number=3):
        multiQuery_template = ChatPromptTemplate.from_template(MULTI_QUERY_PROMPT)
        queries = await (
            multiQuery_template 
            | self.__llm
            | StrOutputParser() 
            | (lambda x: x.split("\n"))
        ).ainvoke({
            "number": number,
            "query": message
        })
        return [doc for doc in queries if doc != '']

    @traceable
    async def __generalize_user_message(self, message):
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
        stepBack_prompt = ChatPromptTemplate.from_messages([
            ("system", STEP_BACK_PROMPT),
            few_shot_prompt,
            ("user", "{query}"),
        ])
        query = await (stepBack_prompt | self.__llm).ainvoke({"query": message})
        return query.content
    
    async def rephrase_user_message(self, message):
        multi_query_resp, step_back_resp = await asyncio.gather(
            self.__paraphrase_user_message(message),
            self.__generalize_user_message(message)
        )
        queries = multi_query_resp + [step_back_resp]
        return queries


class LawTypeIdentifier:
    def __init__(self, llm_model="gpt-4o-mini"):
        self.__structured_llm = ChatOpenAI(model=llm_model, temperature=0).with_structured_output(TopicIDResponse)
        self.__support_law_type = ["civil_law", "criminal_law", "environmental_law", "international_law", "labor_and_employment_law"]
    
    def identify_law_type_from_user_message(self, user_message):
        prompt = LAW_CLASSIFIER_PROMPT.format(message=user_message)
        response = self.__structured_llm.invoke(prompt)
        id = response.id
        return self.__support_law_type[id]
    
