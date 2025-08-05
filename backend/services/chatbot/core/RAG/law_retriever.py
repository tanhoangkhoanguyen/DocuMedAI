from services.chatbot.core.rag.tools import law_classifier, multi_query, step_back, tavily_search, retrieve_doc
from services.chatbot.core.constants.prompts import CONTEXT_QUESTION_PROMPT

import asyncio, warnings, time

warnings.filterwarnings("ignore")
THRESHOLD = 0.49

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
    reliable_docs = []
    unreliable_docs = []
    for subdoc in docs:
        for doc in subdoc:
            if doc.score >= THRESHOLD:
                reliable_docs.append(doc.payload['text'])
            else:
                unreliable_docs.append(doc.payload['text'])
    
    prompt = CONTEXT_QUESTION_PROMPT.format(
        reliable_context = reliable_docs,
        unreliable_context = unreliable_docs,
        website_information = tavily_resp,
        question = message
    )
    return llm.invoke(prompt)