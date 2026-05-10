TOPIC_SUMMARIZED_PROMPT = """
Summarize
{conversation}
"""

# ==================== MessageAnalysis prompt ====================
MESSAGE_ANALYSIS_PROMPT_1 = """
You are a precise information-extraction assistant.
Your task is to read the user's raw text and return a JSON array of one or more objects.

OUTPUT FORMAT
[{
    "context": "string",
    "messages": ["string", ...],
    "instruction": "string"
}]

RULES
- Extract strictly from the user's message. Never add facts, summarize, or merge unrelated topics.
- Each object should represent one coherent topic (based on context). If multiple contexts appear, split them into separate objects.
- For missing data, use "" for string, and [] for list.
- A request can belong to both "messages" and "instruction".
- Minimal normalization only: trim whitespace; do not rephrase.

FIELD EXTRACTION
- context (string): background, situation, or emotion describing *who*, *where*, *when*, *why*, or *how*.
- messages (list[string]): explicit user requests, tasks, or questions related to that context.
- instruction (string): formatting instruction specifying how output should appear.
"""

MESSAGE_ANALYSIS_PROMPT_2 = """
EXAMPLES

User message: "My professor has not replied to my email. Should I send another one?  Also, I am applying for graduate school soon, can you help me review my personal essay? Please list the suggestions in bullet points."
Output:
[{
    "context": "My professor hasn't replied to my email.",
    "messages": ["Should I send another one?"],
    "instruction": ""
  },
  {
    "context": "I am applying for graduate school soon.",
    "messages": ["Can you help me review my personal essay?"],
    "instruction": "List suggestions in bullet points."
}]

User message: "Rewrited your previous response in 1 super short setence."
Output:
[{
    "context": "",
    "messages": ["Rewrited your previous response in 1 super short setence."],
    "instruction": "Rewrited your previous response in 1 super short setence."
}]
"""

# ==================== NodeController prompt ====================
PARAPHRASE_MESSAGE_PROMPT = """
You are an expert in message rephrasing.
Generate {number} versions of the user’s message that keep the original meaning but express it in different ways to improve search coverage.
"""

GENERALIZE_USER_MESSAGE_PROMPT = """
Paraphrase the message into a more general, easier-to-answer version.
EXAMPLE

Input: "Could the members of The Police perform lawful arrests?"
Output: "What can the members of The Police do?"

Input: "Lionel Messi's was born in what country?"
Output: "What is Lionel Messi's personal history?"
"""

# ==================== Agent (MCP planner + synthesis) ====================
AGENT_PLANNER_PROMPT = """
You are a planning assistant that chooses MCP tools for each user sub-message.

AVAILABLE MCP TOOLS (name + description):
{tool_catalog}

RULES
- A task may require MULTIPLE tool_calls.
- Each tool_call must name an exact tool from the list and include a short "message" string: the specific input or rewritten query for that tool only.
- Use memory/context fields only to disambiguate.
- If no tool fits, return an empty tool_calls list.

CONTEXT FOR THIS TASK:
{context}

USER SUB-MESSAGE TO SATISFY:
{message}
"""

AGGREGATE_AND_REVISE_PROMPT = """
You combine several tool/MCP result snippets into one coherent answer for the user.

OUTPUT RULES
- Merge content clearly (sections or bullets when helpful).
- Apply FORMATTING / STYLE instruction below strictly when non-empty.

USER CONTEXT (may be empty):
{context}

USER FORMATTING INSTRUCTION (may be empty):
{instruction}

TOOL RESULTS (each block addresses part of the user's requests):
{blocks}
"""