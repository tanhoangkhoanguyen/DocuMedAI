from services.chatbot.core.constants.schemas import TopicIDResponse
from services.chatbot.core.constants.prompts import MULTI_QUERY_PROMPT, STEP_BACK_PROMPT

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
    async def __paraphrase_user_message(self, query, number=3):
        multiQuery_template = ChatPromptTemplate.from_template(MULTI_QUERY_PROMPT)
        queries = await (
            multiQuery_template 
            | self.__llm
            | StrOutputParser() 
            | (lambda x: x.split("\n"))
        ).ainvoke({
            "number": number,
            "query": query
        })
        return [doc for doc in queries if doc != '']

    @traceable
    async def __generalize_user_message(self, query):
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
        query = await (stepBack_prompt | self.__llm).ainvoke({"query": query})
        return query.content
    
    async def rephrase_user_message(self, query):
        multi_query_resp, step_back_resp = await asyncio.gather(
            self.__paraphrase_user_message(query),
            self.__generalize_user_message(query)
        )
        queries = multi_query_resp + [step_back_resp]
        return queries