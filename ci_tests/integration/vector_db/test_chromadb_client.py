import tempfile

import pytest

from ci_tests.fixtures.vector_db import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    EMBEDDED_QUERY,
    run_vector_db_test,
)
from vector_database_tests.utils.chromadb_client import ChromadbClient


pytestmark = [pytest.mark.integration, pytest.mark.vectordb]


@pytest.fixture
def chroma_persist_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("CHROMA_PERSIST_DIR", tmp)
        yield tmp


def test_chromadb_retrieval(chroma_persist_dir):
    client = ChromadbClient(
        embedding_model=EMBEDDING_MODEL,
        embedding_dimension=EMBEDDING_DIMENSION,
    )
    run_vector_db_test(client, "LatencyTest", EMBEDDED_QUERY)
