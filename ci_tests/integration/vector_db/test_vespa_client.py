import pytest

from ci_tests.fixtures.vector_db import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    EMBEDDED_QUERY,
    run_vector_db_test,
)
from vector_database_tests.utils.vespa_client import VespaClient


pytestmark = [pytest.mark.integration, pytest.mark.vectordb]


def test_vespa_retrieval():
    client = VespaClient(
        embedding_model=EMBEDDING_MODEL,
        embedding_dimension=EMBEDDING_DIMENSION,
    )
    run_vector_db_test(client, "LatencyTest", EMBEDDED_QUERY)
