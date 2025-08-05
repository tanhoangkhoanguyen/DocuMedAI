from playground.khoanth.rag.tools import law_classifier, multi_query, step_back, tavily_search, retrieve_doc, reranker
from playground.khoanth.prompts import CONTEXT_QUESTION_PROMPT

import asyncio, warnings, time

warnings.filterwarnings("ignore")

async def invoke_law_advisor(llm, structured_llm, message):
    """
    1. classify law types
    2. generate response
    """
    start_time = time.time()
    law_type = law_classifier(structured_llm, message)
    multi_query_resp, step_back_resp, tavily_resp = await asyncio.gather(
        multi_query(llm, message),
        step_back(llm, message),
        tavily_search(message)
    )

    queries = multi_query_resp + [step_back_resp]
    docs = await asyncio.gather(*(retrieve_doc(q, law_type) for q in queries))
    reliable_docs, unreliable_docs = reranker(message, docs)
    
    prompt = CONTEXT_QUESTION_PROMPT.format(
        reliable_context = reliable_docs,
        unreliable_context = unreliable_docs,
        website_information = tavily_resp,
        question = message
    )
    return llm.invoke(prompt).content