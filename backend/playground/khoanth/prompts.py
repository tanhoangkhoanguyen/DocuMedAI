INTENT_DETECTOR_PROMPT = """
You are a smart intent classifier assistant. 
Your task is to classify the user's message into one of the categories below. 
Think carefully based on the provided keywords and select the most compatible option (prioritize the smaller number option).
Respond a single integer.

### Categories:

#### 0 - Greeting
Descriptions: The user's message may includes some greeting messages such as "Hi", "Hello" but not ask about any problem.

#### 1 - Complaint Request
Descriptions: The user's message includes the complaints about the services or the previous response.

#### 2 - Instruction Support
Descriptions: The user's message specify any requirements about the response, for example response in mark down or give the short answer.

#### 3 - Law Support
Descriptions: The user's message asks about the law and may includes some keywords, such as "law", "lawyer", "legal", "sue", "court".

#### 4 - Casual Chat
Descriptions: The user's message does not match any previous categories (Greeting, Complaint Request, Instruction Support, or Law Support). The user's message might starts with any greetings words, such as "hi", "hello" but they DO ASK about one or more specific question(s).
  
User Message: "{message}"
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

MULTI_QUERY_PROMPT = """
You are an expert semantic search engineer.
Your task is to rephrased {number} versions of the user question to improve vector search results by covering different perspectives. 
Separate each with a newline.
Original question: {query}
"""

STEP_BACK_PROMPT = "Step back and paraphrase the question into a more general, easier-to-answer version. Examples:"


CONTEXT_QUESTION_PROMPT = """
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