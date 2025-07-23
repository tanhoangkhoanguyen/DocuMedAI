RESOLVED_INTENT_DETECTOR_PROMPT = """
You are a smart intent classifier assistant. 
Your task is to classify the user's message into one of the categories below. 
Think carefully based on the provided descriptions, keywords, examples and select the most compatible option (prioritize the smaller number option).
Respond a single integer.

### Categories:
#### 0 - Law Advisory
Description: Any message that is STRICTLY related to legal matters
Keywords: "legal advice/right", "lawyer/attorney", "file a case", "sue", "court"
Examples:
- "My employer is not paying me overtime. What can I do?"
- "My neighbor built a fence that goes over my property line. What should I do?"
- "Is it okay to record a phone conversation without telling the other person?"

#### 1 - Other
Description: Any message that falls outside the scope of legal advisory.
Examples:
- "What's the weather like in Tokyo?"
- "Tell me a joke."
- "Convert 100 USD to EUR."

User Message: "{message}"
"""


ADVISORY_TYPES_PROMPT = """
You are a smart law classifier assistant.
Your task is to classify the user's message into one of the categories below.
Think carefully based on the provided keywords, examples and select the most compatible option (prioritize the smaller number option).
Respond a single integer.

### Categories:
#### 0 - personal_law
Subcategories:
- civil_law - private disputes, contracts, inheritance, liability, family law.
- criminal_law - crime, arrest, sentencing, sexual offenses theft, drug trafficking.
Examples:
"Can I sue my landlord for wrongfully withholding my security deposit?"
"Can I file a civil lawsuit or restraining order against my neighbor for repeated trespassing on my property?"
"Is punching someone considered criminal assault, or could it be self-defense?"
"What is the maximum prison sentence for felony assault?"

#### 1 - global_law
Subcategories:
environmental_law - pollution, environmental permits, EPA violation, carbon credits, emissions
international_law - WTO rules, border laws, human rights, Paris agreement, immigration law
Examples:
"What penalties exist for illegal dumping of toxic waste under the Clean Water Act?"
"Do corporations need an EPA permit to emit greenhouse gases?"
"Can a country be sanctioned for violating UN human rights treaties?"
"What legal protections do refugees have under the 1951 Convention?"

#### 2 - labor_and_employment_law
Subcategories:
labor_and_employment_law - employee rights, workplace safety, workplace discrimination, wages, wrongful termination
Examples:
"Can my employer fire me without cause in an at-will employment state?"
"Is it illegal to pay women less than men for the same job?"
"Am I entitled to overtime pay if I work 50 hours a week?"
"Does my company have to provide accommodations for my disability?"

User Message: "{message}"
"""


PERSONAL_TYPES_PROMPT = """
You are a smart law classifier assistant.
Your task is to classify the user's message into one of the categories below.
Think carefully based on the provided keywords, examples and select the most compatible option (prioritize the smaller number option).
Respond a single integer.

### Categories:
#### 0 - civil_law
Keywords: "private disputes", "contracts", "inheritance", "liability", "family law".
Examples:
- "Can I sue my landlord for not fixing the heater?"
- "I want to file for divorce."
- "I am disputing an inheritance issue with my siblings."

#### 1 - criminal_law
Keywords: "crime", "arrest", "sentencing", "sexual offenses theft", "drug trafficking".
Examples:
- "Is drunk driving a criminal offense?"
- "Can I get a lawyer if I am arrested for theft?"
- "I was falsely accused of assault."

User Message: "{message}"
"""


GLOBAL_TYPES_PROMPT = """
You are a smart law classifier assistant.
Your task is to classify the user's message into one of the categories below.
Think carefully based on the provided keywords, examples and select the most compatible option (prioritize the smaller number option).
Respond a single integer.

### Categories:
#### 0 - environmental_law
Keywords: "pollution", "environmental permits", "EPA violation", "carbon credits", "emissions".
Examples:
- "What penalties exist for illegal dumping of toxic waste under the Clean Water Act?"
- "Do corporations need an EPA permit to emit greenhouse gases?"
- "How does CITES protect endangered species in international trade?"

#### 1 - international_law
Keywords: "WTO rules", "border laws", "human rights", "Paris agreement", "immigration law"
Examples:
- "Is the Paris Agreement legally binding on signatory countries?"
- "Can a country be sanctioned for violating UN human rights treaties?"
- "What legal protections do refugees have under the 1951 Convention?"

User Message: "{message}"
"""


MULTI_QUERY_PROMPT = """
You are an expert semantic search engineer.
Your task is to rephrased {number} versions of the user question to improve vector search results by covering different perspectives. 
Separate each with a newline.
Original question: {question}
"""


STEP_BACK_PROMPT = "Step back and paraphrase the question into a more general, easier-to-answer version. Examples:"


CONTEXT_QUESTION_PROMPT = """
You are a professional legal assistant.
Your task is to answer the following question thoroughly and accurately.

Use the following context if relevant; ignore if not. Prioritize earlier information over later, in descending importance
- normal_context - derived from rephrasings of the original question to improve search coverage.
- general_context - a broader, reframed version of the question. It helps provide background, prevent hallucination, especially when the original question is overly specific.

Prioritize earlier information over later, in descending importance:

- normal_context: {normal_context}
- general_context: {general_context}
- original_question: {original_question}
"""