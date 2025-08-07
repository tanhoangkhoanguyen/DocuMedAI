from playground.khoanth.rag.tools import law_classifier, multi_query, step_back, tavily_search, qdrantSearch, elasticSearch, remove_similar_documents, rerank
from playground.khoanth.prompts import CONTEXT_QUESTION_PROMPT

import asyncio, warnings, time

warnings.filterwarnings("ignore")

async def invoke_law_advisor(llm, structured_llm, message):
    """
    1. classify law types
    2. generate response
    """
    law_type = law_classifier(structured_llm, message)
    multi_query_resp, step_back_resp, tavily_resp = await asyncio.gather(
        multi_query(llm, message),
        step_back(llm, message),
        tavily_search(message)
    )

    rerank_docs = []
    queries = multi_query_resp + [step_back_resp]

    print ("Start the retrieval process")
    qdrant_docs = await asyncio.gather(*(qdrantSearch(query, law_type) for query in queries))
    for sub in qdrant_docs:
        for doc in sub:
            rerank_docs.append(doc.payload['text'])

    elastic_docs = await asyncio.gather(*(elasticSearch(query, law_type) for query in queries))
    for sub in elastic_docs:
        for doc in sub:
            rerank_docs.append(doc['_source']['text'])

    reliable_docs, unreliable_docs = rerank(message, remove_similar_documents(rerank_docs))
    prompt = CONTEXT_QUESTION_PROMPT.format(
        reliable_context = reliable_docs,
        unreliable_context = unreliable_docs,
        website_information = tavily_resp,
        question = message
    )
    return llm.invoke(prompt).content