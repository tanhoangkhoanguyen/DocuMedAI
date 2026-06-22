import pytest

from ci_tests.fixtures.vector_db import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    EMBEDDED_QUERY,
    run_vector_db_test,
)
from vector_database_tests.utils.pinecone_client import PineconeClient


pytestmark = [pytest.mark.integration, pytest.mark.vectordb]


def test_pinecone_retrieval():
    client = PineconeClient(
        embedding_model=EMBEDDING_MODEL,
        embedding_dimension=EMBEDDING_DIMENSION,
    )
    # Pinecone requires lowercase index names.
    run_vector_db_test(client, "latencytest", EMBEDDED_QUERY)
