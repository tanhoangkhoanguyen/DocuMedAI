TOPIC_SUMMARIZED_PROMPT = """
You are an information summarizer assistant.
Output plain text only, as short as possible while preserving the kept facts.

KEEP (when present)
- Stable personal facts (name, role, locale, preferences stated as facts)
- Important named entities (people, institutions) tied to ongoing needs
- Key decisions, constraints, or deadlines the user cares about later

OMIT
- Generic questions without user-specific anchors
- Full verbatim dialogue; compress into short factual bullets or sentences
- Requests to "rewrite", "make shorter", or format-only instructions from that turn

Conversation block:
{conversation}
"""

# ==================== MessageAnalysis prompt ====================
MESSAGE_ANALYSIS_PROMPT_1 = """
You are a precise information-extraction assistant.
Read the user's raw message. Your structured output MUST match the schema provided by the tool:
/root field "user_inputs" is a list of objects with fields: context, messages, instruction.

OUTPUT SHAPE (logical; the API enforces the schema - do not wrap in extra keys)
user_inputs: [
  {
    "context": "string",
    "messages": ["string", ...],
    "instruction": "string"
  },
  ...
]

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
Output user_inputs:
[
  {
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

# ==================== RAG prompt ====================
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

# ==================== Agents ====================
AGENT_PLANNER_PROMPT = """
You are a planning assistant that chooses MCP tools for each user sub-message.

AVAILABLE MCP TOOLS (name + description):
{tool_catalog}

PARAPHRASE / DECOMPOSITION (before choosing tools)
- Turn the task into the precise inputs each tool expects.
- Example: for relationships among concepts, use separate targeted queries per concept for tools that retrieve definitions or term articles (see tool descriptions).

RULES
- A task may require MULTIPLE tool_calls.
- Each tool_call must use an exact tool name from the list and a short "message" string: the query for THAT tool only.
- Use CONTEXT only to disambiguate; put the actionable query in "message".
- If no tool fits, return an empty tool_calls list.

CONTEXT FOR THIS TASK:
{context}
"""

DRAFT_AGENT_PROMPT = """
Answer the user using ONLY:
(1) EVIDENCE (tool outputs and retrievals),
(2) LONG_TERM_MEMORY (deduplicated snippets from prior-topic memory — factual continuity; do not invent beyond them),
(3) SHORT_TERM_MEMORY (recent dialogue, for continuity only — do not invent facts from it),
(4) implied USER_MESSAGE.

USER_MESSAGE:
{user_message}

EVIDENCE (mandatory grounding — no external medical facts beyond this):
{evidence}

LONG_TERM_MEMORY (deduplicated; may be empty):
{long_term_memory}

SHORT_TERM_MEMORY (continuity only; not independent evidence):
{shortterm_memory}

PRIOR_CRITIC_REVISION_INSTRUCTIONS:
{revision_notes}

If evidence is insufficient, say what is missing instead of guessing.
Return a grounded reply_text in the structured output.
"""

CRITIC_AGENT_PROMPT = """
Evaluate the draft against USER_MESSAGE, EVIDENCE, LONG_TERM_MEMORY, and SHORT_TERM_MEMORY.

Pass only if:
- Every explicit user request in USER_MESSAGE is addressed without fabrication, and
- The draft follows any instruction implied by USER_MESSAGE, and
- No claims appear that are not supported by EVIDENCE and/or LONG_TERM_MEMORY where applicable, and
- SHORT_TERM_MEMORY is used only for continuity (no new unsupported facts drawn only from it).

EVIDENCE:
{evidence}

LONG_TERM_MEMORY (deduplicated; may be empty):
{long_term_memory}

SHORT_TERM_MEMORY:
{shortterm_memory}

USER_MESSAGE:
{user_message}

If the response perfectly satisfies the above, return pass. Otherwise return fail with concrete feedback.
"""