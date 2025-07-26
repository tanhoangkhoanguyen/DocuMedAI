CONVERSATIONAL_INTENT_DETECTOR_PROMPT = """
You are a smart intent classifier assistant. 
Your task is to classify the user's message into one of the categories below. 
Think carefully based on the provided keywords, examples and select the most compatible option (prioritize the smaller number option).
Respond a single integer.

### Categories:
#### 0 - Greeting
Keywords: "yo", "hi", "hey"
Examples: 
- "Is anyone there/here?"
- "Just checking in."
- "Good morning/afternoon/evening!"

#### 1 - Add Instruction
Keywords: "custom", "tailored advice", "personalize", "detailed guidance"
Examples:
- "Respond in a friendly tone."
- "Provide deep reasoning for all your responses."
- "Your task is to explain/extend/summarize whatever I paste next."
- "Use markdown for everything you send."

#### 2 - Complain/Contact
Do NOT select this option if user asks for personal help or describes a situation unrelated to the chatbot, even if the message sounds like a complaint.
- "I am sad"
- "I am bullied"
- "Help me draw a picture"
Keywords: "feedback", "report a problem", "reach out"
Examples:
- "There is a problem with your answers."
- "You/chatbot is dumb."
- "I have a question about the system"
- "This feature does not behave as expected"

User Message: "{message}"
"""


ADD_INSTRUCTION_PROMPT = """
You are a smart assistant. 
The user is expressing preferences about your responses.  
Your task is to extract and return a string of the core requests for style, tone, format, depth, or other customizations.

### Examples:
input_message: "You don't need to explain everything. Try not to sound too formal."  
`instruction`: use an advanced explanation style and a casual tone

input_message: "Give me more real-world examples next time."  
`instruction`: include real-world examples

input_message: "Your task is to summarize."  
`instruction`: for what user paste in, only summarize

### Input
`input_message`: {input_message}
"""