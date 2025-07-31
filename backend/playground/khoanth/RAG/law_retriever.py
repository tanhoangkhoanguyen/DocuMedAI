# from dotenv import load_dotenv
# from langchain_core.tools import tool

# load_dotenv()

# @tool
# def meh(query):
#     """
#     Test `@tool`
#     """
#     print ("urgh")

# meh("Nhi")
from playground.khoanth.RAG.tools import law_classifier, multi_query, step_back, tavily_search, retrieve_doc

import asyncio, warnings, time

warnings.filterwarnings("ignore")

async def invoke_law_advisor(llm, structured_llm, message):
    """
    1. classify law types
    2. generate response
    """
    start_time = time.time()
    law_type = "criminal_law"#law_classifier(structured_llm, message)

    # multi_query_resp, step_back_resp, tavily_resp = await asyncio.gather(
    #     multi_query(llm, message),
    #     step_back(llm, message),
    #     tavily_search(message)
    # )

    queries = ['What is the potential prison sentence for stealing $500?', 'How long could I expect to be incarcerated for a theft of $500?', 'What are the typical legal consequences for stealing an amount of $500?', 'what are the potential legal consequences for theft?']#multi_query_resp + [step_back_resp]
    print(queries)

    docs = [retrieve_doc(query, law_type) for query in queries]
    for doc in docs:
        print(doc)

    print (time.time() - start_time)
    return "yNhi dth"