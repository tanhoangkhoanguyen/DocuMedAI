import pytest

from ci_tests.fixtures.vector_db import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    EMBEDDED_QUERY,
    POINT_ID,
)
from vector_database_tests.utils.milvus_client import MilvusClient


pytestmark = [pytest.mark.integration, pytest.mark.vectordb]


def test_milvus_retrieval():
    client = MilvusClient(
        embedding_model=EMBEDDING_MODEL,
        embedding_dimension=EMBEDDING_DIMENSION,
    )

    collection_name = "LatencyTest"
    try:
        client.create_collection(collection_name)
        client.push_documents(
            collection_name=collection_name,
            ids=[POINT_ID],
            queries=["test query"],
            embedded_queries=[EMBEDDED_QUERY],
        )
        client.create_index(collection_name)
        response = client.retrieve_query(
            collection_name=collection_name,
            embedded_query=EMBEDDED_QUERY,
        )
        assert len(response) > 0
    finally:
        client.delete_collection(collection_name)
