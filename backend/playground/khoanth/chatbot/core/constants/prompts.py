MESSAGE_ANALYSIS_PROMPT = """
You are a smart information-extraction assistant.
Your task is to read the user's raw message and return a single valid JSON object (and nothing else).

REQUIREMENTS
1. Extract strictly from the user's message. Do NOT invent facts or add content that is not present. Minimal normalization only: trim whitespace.
2. If a field cannot be found:
   - For string fields use "".
   - For "messages" uses [].
3. Preserve the original message.

FIELD EXTRACTION RULES
- messages (list[string]): extract every explicit message, request, or question that is NOT part of the context, instruction, or reasoning. Each element must be a plain string. Examples:
    - "What is the duration of my sentence?" -> message
    - "I have just stolen 5000$" -> NOT a message (belongs to context)
    - "Response in markdown format" -> NOT a message (belongs to instruction)
    - "Think carefully before answering me" -> NOT a message (belongs to reasoning)

- context (string): extract background or situational details (who/where/when/how long/emotional state) that help interpret the request. If none, "". Example:
    - "I am a tenant in New York and my landlord has not fixed the broken heating for two weeks."

- instruction (string): extract only output format. Examples:
    - "Response in markdown format"
    - "Use bullet points"
  Do NOT include reasoning. If none present, "".

USER MESSAGE:
{message}
"""

INTENT_ANALYSIS_PROMPT = """
You are a strict intent classifier.  
You will receive a list of user messages and must classify each message into one of the categories below.
Use both the provided global context (conversation history) and local context (the latest user's message). Prioritize the local context when classifying.  
Think carefully based on the keywords and examples, and select the most compatible option (prioritize the smaller number if multiple options fit).  

### Categories:
#### 0 - Law Support
The user's message asks about the law and may include keywords such as "law", "lawyer", "legal", "sue", "court".

#### 1 - Chit chat
The user's message does not match any previous category.

### Context:
- Global Context: {global_context}
- Local Context: {local_context}

### User Messages:
{messages}

Respond with an array of integers (not text, not explanation, no extra text).  
Example: [0, 1, 1, 0]
"""

LAW_CLASSIFIER_PROMPT = """
You are a smart law classifier assistant.
Your task is to classify the user's message into one of the categories below.
Think carefully based on the keywords and context, and choose the most suitable category.
If unsure between two categories, select the one with the smaller number.
Respond with a single integer only.

### Categories:
#### 0 - Civil Law
Private matters like divorce, property, contracts, inheritance, and landlord-tenant disputes.
Example: "Can I sue my landlord for not fixing the heater?"

#### 1 - Criminal Law
Crimes such as theft, assault, drugs, arrest, or prison sentences.
Example: "What is the punishment for breaking into a house?"

#### 2 - Environmental Law
Issues about pollution, emissions, EPA, endangered species, or environmental regulations.
Example: "Do factories need permits to release carbon emissions?"

#### 3 - International Law
Topics about immigration, treaties, human rights, or global legal issues.
Example: "What rights do refugees have under international law?"

#### 4 - Labor and Employment Law
Workplace-related matters like wages, discrimination, safety, or wrongful termination.
Example: "Can I be fired for taking sick leave?"

User Message: "{message}"
"""

PARAPHRASE_USER_MESSAGE_PROMPT = """
You are an expert semantic search engineer.
Your task is to rephrased {number} versions of the user question to improve vector search results by covering different perspectives. 
Separate each with a newline.
Original question: {query}
"""

GENERALIZE_USER_MESSAGE_PROMPT = "Step back and paraphrase the question into a more general, easier-to-answer version. Examples:"

LAW_GENERATION_PROMPT = """
You are a professional legal assistant.
Your task is to answer the question thoroughly and accurately based on the provided context.

### Use the following context if relevant; otherwise, ignore it. Prioritize earlier information over later, in descending importance.
- reliable_context: Highly relevant and trustworthy information.
- unreliable_context: Less relevant, with lower semantic similarity. Can be used as background information.
- website_information: Up-to-date content from the web.

### Input:
- reliable_context: {reliable_context}
- unreliable_context: {unreliable_context}
- website_information: {website_information}
- question: {question}
"""

CHIT_CHAT_PROMPT = """
You are a snart assistant
Your task is to answer user_message given the message_context and chat_history

### Input:
- User message: {user_message}
- Message context: {local_context}
- Chat history: {global_context}
"""

SYNTHESIS_PROMPT = """
You are a smart writer.
Your task is to write a final response based on the list of contexts I provide.
Each element in the list is an answer to a separate user question, so make sure to separate these answers clearly.
Strictly follow the given instruction when creating the final response.

### Input:
- Context list: {context_list}
- Instruction: {instruction}
"""

LOCAL_SUMMARIZER_PROMPT = """
You are a smart summarizer.  
Your task is to merge local and global inputs into two paragraphs.

The input will have 2 group of input information:
1. Context (local context + global context)
    Local context: {local_context} 
    Global context: {global_context} 
2. Instruction (local instruction + global instruction) 
    Local instruction: {local_instruction} 
    Global instruction: {global_instruction}     

Rules:  
- Always prioritize local over global.
- For global: keep all info, but summarize earlier details briefly and give more weight to later details.  

Output:  
Paragraph 1 = merged context (single string)  
Paragraph 2 = merged instruction (single string)
"""



