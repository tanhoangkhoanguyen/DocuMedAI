from playground.khoanth.prompts import *
from services.chatbot.core.constants.schemas import LawAgentState, TopicIDResponse

import os, asyncio, requests, warnings
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable
from langchain_core.tools import tool
from langchain_community.embeddings import OpenAIEmbeddings, HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate
from qdrant_client import QdrantClient

warnings.filterwarnings("ignore")
load_dotenv()

class LawAdvisor(Runnable):
    def __init__(self, model_name:str, temperature:int=0):
        self.llm = ChatOpenAI(model=model_name, temperature=temperature)
        self.structured_llm = ChatOpenAI(model=model_name, temperature=temperature).with_structured_output(TopicIDResponse)
        # Tools
        self.law_classifier = tool(self._law_classifier)
        self.multi_query = tool(self._multi_query)
        self.step_back = tool(self._step_back)
        self.tavily_search = tool(self._tavily_search)

    def _law_classifier(self, message):
        """
        Classify the user message into one of the defined legal categories using the LLM.
        """
        prompt = LAW_CLASSIFIER_PROMPT.format(message = message)
        response = self.structured_llm.invoke(prompt).id
        return [
            "civil_law",
            "criminal_law",
            "environmental_law",
            "international_law",
            "labor_and_employment_law"
        ][response]
    
    def retrieve_doc(self, query, collection_name, top_k = 3, threshold = 0.1):
        """
        Retrieve documents from Qdrant database
        """
        client = QdrantClient(
            url = os.getenv("QDRANT_URL"),
            api_key = os.getenv("QDRANT_API_KEY")
        )
        embedding_model = HuggingFaceEmbeddings(model_name = "sentence-transformers/all-MiniLM-L6-v2")
        try:
            query_vector = embedding_model.embed_query(query)
            results = client.search(
                collection_name = collection_name,
                query_vector = query_vector,
                limit = top_k,
                with_payload = True, # include metadata
                with_vectors = False # dont load the whole text
            )
            filtered = [
                res.payload['text']
                for res in results
                if res.score >= threshold
            ]
            return filtered
        except Exception as e:
            print(f"Error querying Qdrant: {e}")
            return []
        finally:
            client.close()

    async def _multi_query(self, query, number = "3"):
        """
        Paraphrase different version of the query
        """
        await asyncio.sleep(0.01)
        multiQuery_template = ChatPromptTemplate.from_template(MULTI_QUERY_PROMPT)
        multiQuery_resp = ( # Convert to RunnableConfig
            multiQuery_template 
            | self.llm 
            | StrOutputParser() 
            | (lambda x: x.split("\n"))
        ).invoke({
            "number": number,
            "query": query
        })
        refined_resp = [doc for doc in multiQuery_resp if doc != ""]
        return refined_resp
    
    async def _step_back(self, query):
        """
        Generate general version of the query
        """
        await asyncio.sleep(0.01)
        examples = [
            {
                "input": "Could the members of The Police perform lawful arrests?",
                "output": "what can the members of The Police do?",
            },
            {
                "input": "Lionel Messi's was born in what country?",
                "output": "what is Lionel Messi's personal history?",
            },
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
            (
                "system",
                STEP_BACK_PROMPT,
            ),
            few_shot_prompt,
            ("user", "{query}"),
        ])
        stepBack_resp = ( # Convert to RunnableConfig
            stepBack_prompt 
            | self.llm 
        ).invoke({"query": query})
        return stepBack_resp.content

    async def _tavily_search(self, query, max_results = 3):
        """
        Web info search
        """
        await asyncio.sleep(0.01)
        TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
        url = "https://api.tavily.com/search"
        headers = {
            "Authorization": f"Bearer {TAVILY_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "query": query,
            "search_depth": "advanced",
            "include_answer": False,
            "max_results": max_results
        }
        response = requests.post(
                url, 
                json = payload, 
                headers = headers
        )
        return response.json()

    async def invoke(self, state:LawAgentState, config = None):
        message = state.messages[-1].content
        law_type = self.law_classifier.invoke({"message": message})

        multi_query_task = self.multi_query.ainvoke({"query": message})
        step_back_task = self.step_back.ainvoke({"query": message})
        tavily_search_task = self.tavily_search.ainvoke({"query": message})

        multi_query, step_back, tavily_search = await asyncio.gather(
            multi_query_task,
            step_back_task,
            tavily_search_task
        )

        queries = multi_query + [step_back]
        print (queries)
        docs = [self.retrieve_doc(query, law_type) for query in queries]
        
        for doc in docs:
            print (doc)

        return state

if __name__ == "__main__":
    Nhi = LawAdvisor("gpt-4o-mini")
    initial_state = LawAgentState(
        messages = [HumanMessage(content = "Thief")]
    )
    resp = asyncio.run(Nhi.invoke(initial_state))
    print (resp)