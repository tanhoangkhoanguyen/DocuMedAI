"""
Minimal CrewAI harness to inspect Task.output after kickoff.

Run from repo backend root (inside Docker: WORKDIR is usually /backend):

  python -m services.chatbot.untitled

Optional: CREW_TEST_MODEL=gpt-4o-mini (default) or gemini-2.5-flash, etc.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from crewai import Agent, Crew, LLM, Process, Task
from dotenv import load_dotenv

# Load project-root .env when run as file or module (__file__ -> .../backend/services/chatbot/untitled.py)
_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
if _ROOT_ENV.is_file():
    load_dotenv(_ROOT_ENV)
load_dotenv()

from services.chatbot.constants.schemas import DraftAgentState


def _crew_llm_model(chat_model: str) -> str:
    if "/" in chat_model:
        return chat_model
    if chat_model.startswith("gpt"):
        return f"openai/{chat_model}"
    if chat_model.startswith("gemini"):
        return f"gemini/{chat_model}"
    return f"openai/{chat_model}"


def _dump_object(raw: Any) -> None:
    print("--- Task.output introspection ---")
    print("type:", type(raw))
    print("repr:", raw)
    if raw is None:
        return
    for name in ("model_dump", "dict"):
        fn = getattr(raw, name, None)
        if callable(fn):
            try:
                out = fn()
                print(f"{name}():", out)
            except Exception as exc:
                print(f"{name}() raised:", exc)
    if isinstance(raw, dict):
        print("as dict:", raw)
    if isinstance(raw, str):
        print("as str:", raw)
    for attr in ("reply_text", "replyText", "raw", "content"):
        if hasattr(raw, attr):
            try:
                print(f"getattr(..., {attr!r}):", getattr(raw, attr))
            except Exception as exc:
                print(f"getattr(..., {attr!r}) raised:", exc)
    try:
        parsed = DraftAgentState.model_validate(raw, from_attributes=True)
        print("DraftAgentState.model_validate(..., from_attributes=True):", parsed)
    except Exception as exc:
        print("DraftAgentState.model_validate(from_attributes) failed:", exc)
    try:
        if hasattr(raw, "model_dump") and callable(raw.model_dump):
            parsed = DraftAgentState.model_validate(raw.model_dump())
            print("DraftAgentState.model_validate(model_dump()):", parsed)
    except Exception as exc:
        print("DraftAgentState.model_validate(model_dump()) failed:", exc)
    if hasattr(raw, "dict") and callable(getattr(raw, "dict")):
        try:
            parsed = DraftAgentState.model_validate(raw.dict())
            print("DraftAgentState.model_validate(dict()):", parsed)
        except Exception as exc:
            print("DraftAgentState.model_validate(dict()) failed:", exc)
    raw_json = getattr(raw, "raw", None)
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            parsed = DraftAgentState.model_validate_json(raw_json.strip())
            print("DraftAgentState.model_validate_json(task.output.raw):", parsed)
        except Exception as exc:
            print("DraftAgentState.model_validate_json(raw) failed:", exc)


def main() -> int:
    try:
        import crewai

        print("crewai version:", getattr(crewai, "__version__", "unknown"))
    except Exception:
        pass

    chat_model = os.environ.get("CREW_TEST_MODEL", "gpt-4o-mini")
    print("CREW_TEST_MODEL:", chat_model)

    llm = LLM(model=_crew_llm_model(chat_model), temperature=0)
    agent = Agent(
        role="Echo assistant",
        goal="Reply briefly to the user.",
        backstory="You answer in JSON matching the expected schema.",
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )
    task = Task(
        description='User said: "Hello". Reply with one short friendly sentence in the schema.',
        expected_output="JSON with key reply_text only.",
        agent=agent,
        output_pydantic=DraftAgentState,
    )
    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=True,
    )
    print("--- kickoff() ---")
    crew.kickoff()

    raw = getattr(task, "output", None)
    _dump_object(raw)

    # Also show crew.result if present (some versions expose final output here)
    cr = getattr(crew, "result", None) or getattr(crew, "raw", None)
    if cr is not None:
        print("--- crew.result / crew.raw ---")
        print(repr(cr))

    return 0


if __name__ == "__main__":
    sys.exit(main())
