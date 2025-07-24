import os, re, requests, json
from operator import itemgetter
from typing import Any
from pprint import pprint
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_core.output_parsers import StrOutputParser
from langchain.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate
from langchain.load import dumps, loads
from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from langchain_community.vectorstores import Chroma
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragatouille import RAGPretrainedModel


from src.state_schema import State, IntOuput
from .resolved_prompt import *
from .keywords import tag


from dotenv import load_dotenv, find_dotenv
_ = load_dotenv(find_dotenv())
chat = ChatOpenAI(model = 'gpt-4o-mini', temperature = 0)
# GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# chat = ChatGroq(
#     model_name = "allam-2-7b",
#     groq_api_key = GROQ_API_KEY,
#     temperature = 0
# )
chain = chat.with_structured_output(IntOuput, method = "function_calling")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


# ========== CONDITIONAL NODES ==========
def resolved_decide_node(state):
    topic_id = state.get('topic_id', 1)
    if topic_id == 0:
        return "law_advisory"
    elif topic_id == 1:
        return "other"


# ========== NODES ==========
def resolved_intent_detector(state):
    message = state['messages'][-1].content
    intentDetector_prompt = RESOLVED_INTENT_DETECTOR_PROMPT.format(message = message)
    response = chain.invoke(intentDetector_prompt)
    
    state['topic_id'] = response['id']
    return state


def other(state):
    other_prompt = "I am sorry, this question is out-of my scope."
    print (other_prompt)
    return {'messages': [
        SystemMessage(content = other_prompt)
    ]}


class LawAdvisory(Runnable):
    def __init__(self):
        self.message = ""
        self.chat = ChatOpenAI(model = 'gpt-4o-mini', temperature = 0)
        # self.chat = ChatGroq(
        #     model_name = "allam-2-7b",
        #     groq_api_key = GROQ_API_KEY,
        #     temperature = 0
        # )
        self.chain = self.chat.with_structured_output(IntOuput, method = "function_calling")
        self.RAG = RAGPretrainedModel.from_pretrained("colbert-ir/colbertv2.0")


    def personal_law_types(self):
        globalTypes_prompt = PERSONAL_TYPES_PROMPT.format(message = self.message)
        response = self.chain.invoke(globalTypes_prompt)

        id = response['id']
        law_type = "civil_law" if id < 1 else "criminal_law"
        return law_type


    def global_law_types(self):
        globalTypes_prompt = GLOBAL_TYPES_PROMPT.format(message = self.message)
        response = self.chain.invoke(globalTypes_prompt)

        id = response['id']
        law_type = "environmental_law" if id < 1 else "international_law"
        return law_type
        

    def advisory_category(self):
        advisoryTypes_prompt = ADVISORY_TYPES_PROMPT.format(message = self.message)
        response = self.chain.invoke(advisoryTypes_prompt)
        
        id = response['id']
        law_type = ""
        if id == 0:
            law_type = self.personal_law_types()
        elif id == 1:
            law_type = self.global_law_types()
        elif id == 2:
            law_type = "labor_and_employment_law"

        return law_type
    

    def other(self):
        prompt = "Stay tuned, the labor_and_employment_law feature is coming soon!!!"
        print (prompt)
        return {'messages': [
            SystemMessage(content = prompt)
        ]}
    

    def load_docs(self, law_type):
        folder_path = f"src/problem_resolution/advisory_types/{law_type}"
        document_path = folder_path + "/document.py"
        keywords_list = f"{law_type}_tags"
        cleaned_docs = []
        
        if not os.path.exists(document_path) or os.stat(document_path).st_size == 0:
            for file_name in os.listdir(folder_path):
                if not file_name.endswith(".pdf"):
                    continue

                file_path = os.path.join(folder_path, file_name)
                loader = PyPDFLoader(file_path)
                raw_docs = loader.load()

                for doc in raw_docs:
                    clean = doc.page_content.encode('ascii', errors = 'ignore').decode()
                    if any(keyword in clean.lower() for keyword in tag[keywords_list]):
                        clean = re.sub(r'\s+', ' ', clean)
                        clean = clean.replace('.', '')
                        clean = clean.strip()
                        cleaned_docs.append(Document(page_content = clean, metadata = doc.metadata))
                
            with open(document_path, "w", encoding = "utf-8") as f:
                print (cleaned_docs, file = f)
        else:
            with open(document_path, "r", encoding = "utf-8") as f:
                str_content = f.read()
            cleaned_docs = eval(str_content)
        return cleaned_docs
    

    def setup(self, docs, law_type):
        splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            chunk_size = 200, 
            chunk_overlap = 50
        )
        split = splitter.split_documents(docs) # only accept Document

        vectorstore = Chroma.from_documents(documents = split, 
                                            embedding = OpenAIEmbeddings(),
                                            persist_directory = f"src/problem_resolution/advisory_types/{law_type}/my_chroma_store")
        retriever = vectorstore.as_retriever(search_kwargs = {"k": 3})
        return retriever
    

    def generate_resp(self, retriever):
        def reciprocal_rank_fusion(docs: list[list], k = 60):
            fused_scores = {}
            for sub in docs:
                for rank, doc in enumerate(sub):
                    doc_str = dumps(doc)
                    if doc_str not in fused_scores:
                        fused_scores[doc_str] = 0
                    fused_scores[doc_str] += 1 / (rank + k)

            reranked_docs = [
                (loads(doc), score)
                for doc, score in sorted(fused_scores.items(), key = lambda x: x[1], reverse = True)
            ]
            return reranked_docs[:5]


        multiQuery_prompt = MULTI_QUERY_PROMPT.format(
            number = "3",
            question = self.message
        )
        multiQuery_template = ChatPromptTemplate.from_template(multiQuery_prompt)
        multiQuery_generate = (
            multiQuery_template 
            | self.chat 
            | StrOutputParser() 
            | (lambda x: x.split("\n")) # list (len == 3) of str
            | retriever.map()           # list (len == 3) of list (len == 3) of Document
            | reciprocal_rank_fusion    # list            of tuples (Document, float)
        ).invoke({"question": self.message})
        cleaned_multiQuery = [doc.page_content for doc, _ in multiQuery_generate]
        str_multiQuery = '\n'.join(cleaned_multiQuery)

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
            ("user", "{question}"),
        ])
        stepBack_generate = (
            stepBack_prompt 
            | self.chat 
        ).invoke({"question": self.message})                                # str
        retrieve_stepBack = [retriever.invoke(stepBack_generate.content)]   # list (len == 1) of list (len == 3)
        fusion_stepBack =  reciprocal_rank_fusion (retrieve_stepBack)
        cleaned_stepBack = [doc.page_content for doc, _ in fusion_stepBack]
        str_stepBack = '\n'.join(cleaned_stepBack)

        contextQuestion_template = CONTEXT_QUESTION_PROMPT.format(
            normal_context = str_multiQuery,
            general_context = str_stepBack,
            question = self.message
        )
        response = self.chat.invoke(contextQuestion_template)
        return response.content
    

    def tavily_search(query, max_results = 3):
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


    def wikipedia_search(title: str):
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query",
            "format": "json",
            "titles": title,
            "prop": "extracts",
            "explaintext": True,
        }

        headers = {"User-Agent": "RAGatouille_tutorial/0.0.1 (ben@clavie.eu)"}

        response = requests.get(
            url, 
            params = params, 
            headers = headers
        )
        data = response.json()

        page = next(iter(data["query"]["pages"].values()))
        return page


    def evaluate_resp(self, response, law_type):
        tavily_resp = self.tavily_search(self.message) # dict['results'] of list (len == 3) of dict['content']
        tavily_text = '\n'.join(doc['content'] for doc in tavily_resp['results'])

        full_doc = self.wikipedia_search(law_type)
        self.RAG.index(
            collection = [full_doc],
            index_name = "wiki_search",
            max_document_length = 200,
            split_documents = True,
        )
        wiki_chat = self.RAG.as_langchain_retriever(k = 3)
        wiki_text = wiki_chat.invoke("message")

        eval_prompt = EVAL_PROMPT.format(
            tavily_context = tavily_text,
            wiki_context = wiki_text,
            question = self.message,
            original_answer = response
        )
        resp = self.chat.invoke(eval_prompt)
        return resp
    

    def invoke(self, state):
        self.message = state['messages'][-1].content

        law_type = "criminal_law" # self.advisory_category()
        if law_type != "criminal_law":
            return other()
        docs = self.load_docs(law_type)
        retriever = self.setup(docs, law_type)
        response = self.generate_resp(retriever)
        final_resp = self.evaluate_resp(response, law_type)

        return {'messages': [
            AIMessage(content = final_resp)
        ]}
    

if __name__ == "__main__":
    Nhi = LawAdvisory()
    Nhi.invoke({'messages': [HumanMessage(content = "If I commit a crime (steal property), how long will I be sentenced?.")]})