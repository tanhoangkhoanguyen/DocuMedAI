"""
Central resolver for Vertex AI configuration.

Why this exists
---------------
All chat-completion traffic from the chatbot graph now runs on Google Vertex
AI. Vertex needs a GCP ``project`` and ``location`` at client construction  time

Configuration
-------------
- ``GOOGLE_CLOUD_PROJECT``  — GCP project id (required for Vertex).
- ``GOOGLE_CLOUD_LOCATION`` — region, defaults to ``us-central1``.

Authentication uses Application Default Credentials (ADC): a service-account
key referenced by ``GOOGLE_APPLICATION_CREDENTIALS``, or the ambient GCP
identity. No API key is passed for Vertex.

NOTE on CrewAI: ``crewai.LLM`` uses litellm under the hood, which reaches
Vertex with the ``vertex_ai/<model>`` prefix and reads the same
``GOOGLE_CLOUD_PROJECT`` / ``GOOGLE_CLOUD_LOCATION`` env vars.
"""

import os


def get_vertex_project() -> str | None:
    """GCP project id for Vertex AI (from ``GOOGLE_CLOUD_PROJECT``)."""
    return os.getenv("GOOGLE_CLOUD_PROJECT")


def get_vertex_location() -> str:
    """Vertex AI region (from ``GOOGLE_CLOUD_LOCATION``; defaults us-central1)."""
    return os.getenv("GOOGLE_CLOUD_LOCATION")
