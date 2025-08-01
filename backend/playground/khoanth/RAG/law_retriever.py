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
THRESHOLD = 0.49

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
    # docs = [retrieve_doc(query, law_type) for query in queries]
    docs = await asyncio.gather(*(retrieve_doc(q, law_type) for q in queries))
    reliable_docs = []
    unreliable_docs = []
    for subdoc in docs:
        for doc in subdoc:
            if doc.score >= THRESHOLD:
                reliable_docs.append(doc.payload['text'])
            else:
                unreliable_docs.append(doc.payload['text'])
    print (reliable_docs)
    print ()
    print (unreliable_docs)
    print (time.time() - start_time)

    return "yNhi dth"

# filtered = [
#                 res.payload['text']
#                 for res in results
#                 if res.score >= threshold
#             ]