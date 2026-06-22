import pytest

from ci_tests.fixtures.vector_db import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    EMBEDDED_QUERY,
    run_vector_db_test,
)
from vector_database_tests.utils.weaviate_client import WeaviateClient


pytestmark = [pytest.mark.integration, pytest.mark.vectordb]


def test_weaviate_retrieval():
    client = WeaviateClient(
        embedding_model=EMBEDDING_MODEL,
        embedding_dimension=EMBEDDING_DIMENSION,
    )
    run_vector_db_test(client, "LatencyTest", EMBEDDED_QUERY)
