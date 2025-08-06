from playground.khoanth.prompts import LAW_CLASSIFIER_PROMPT, MULTI_QUERY_PROMPT, STEP_BACK_PROMPT

import os, requests, asyncio, torch, warnings
from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import Runnable
from langchain_core.tools import tool
from langchain_community.embeddings import OpenAIEmbeddings, HuggingFaceEmbeddings
from langchain.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate
from langsmith import traceable
from qdrant_client import QdrantClient
from transformers import AutoTokenizer, AutoModelForSequenceClassification

load_dotenv()
warnings.filterwarnings("ignore")
THRESHOLD = -6
# qdrant retrieval
embedding_model = HuggingFaceEmbeddings(model_name = "sentence-transformers/all-MiniLM-L6-v2")
# reranker
reranker_modelName = "BAAI/bge-reranker-v2-m3"
tokenizer = AutoTokenizer.from_pretrained(reranker_modelName)
model = AutoModelForSequenceClassification.from_pretrained(reranker_modelName).eval()
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model = model.to(device)

@traceable
def law_classifier(structured_llm, message: str) -> str:
    """
    Classify query's law type
    """
    prompt = LAW_CLASSIFIER_PROMPT.format(message = message)
    response = structured_llm.invoke(prompt).id
    return ["civil_law", "criminal_law", "environmental_law", "international_law", "labor_and_employment_law"][response]
    
def blocking_retrieve(query, collection_name, top_k = 3):
    try:
        client = QdrantClient(
            url = os.getenv("QDRANT_URL"),
            api_key = os.getenv("QDRANT_API_KEY")
        )
        query_vector = embedding_model.embed_query(query)
        results = client.search(
            collection_name = collection_name,
            query_vector = query_vector,
            limit = top_k,
            with_payload = True,
            with_vectors = False
        )
        return results
    except Exception as e:
        print(f"Error querying Qdrant: {e}")
        return []
    finally:
        client.close()

async def qdrantSearch(query, collection_name, top_k = 3):
    return await asyncio.to_thread(blocking_retrieve, query, collection_name, top_k)

def elasticSearch(query, size = 3):
    try:
        search_body = {
            "query": {
                "match": {
                    "text": {
                        "query": query,
                        "fuzziness": "AUTO"
                    }
                }
            },
            "size": size
        }
        search_response = client.search(
            index = index_name,
            body = search_body
        )
        return search_response['hits']['hits']
    except Exception as e:
        print(f"Search error: {e}")
        return []

@traceable
async def multi_query(llm, query, number = "3"):
    multiQuery_template = ChatPromptTemplate.from_template(MULTI_QUERY_PROMPT)
    queries = await (
        multiQuery_template 
        | llm
        | StrOutputParser() 
        | (lambda x: x.split("\n"))
    ).ainvoke({
        "number": number,
        "query": query
    })
    return [doc for doc in queries if doc != '']

@traceable
async def step_back(llm, query):
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
    query = await (stepBack_prompt | llm).ainvoke({"query": query})
    return query.content

@traceable
async def tavily_search(query, max_results = 3):
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
    try:
        response = await asyncio.to_thread(requests.post, url, json = payload, headers = headers)
        return response.json()
    except Exception as e:
        return {"error": str(e)}
    
def rerank(query, passages, top_k = 5):
    pairs = [[query, passage] for passage in passages]
    inputs = tokenizer(pairs, padding = True, truncation = True, return_tensors = "pt").to(device)
    inputs = inputs.to(device)
    # Get relevance scores (logits)
    with torch.no_grad():
        scores = model(**inputs).logits.squeeze(-1)
    # Sort scores in descending order
    sorted_indices = torch.argsort(scores, descending = True)
    reliable_docs = []
    unreliable_docs = []
    for i in sorted_indices[:top_k]:
        if scores[i].item() >= THRESHOLD:
            reliable_docs.append(passages[i])
        else:
            unreliable_docs.append(passages[i])
    return reliable_docs, unreliable_docs