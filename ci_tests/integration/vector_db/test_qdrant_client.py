import pytest

from ci_tests.fixtures.vector_db import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    EMBEDDED_QUERY,
    run_vector_db_test,
)
from vector_database_tests.utils.qdrant_client import QdrantClient


pytestmark = pytest.mark.integration


def test_qdrant_retrieval():
    client = QdrantClient(
        embedding_model=EMBEDDING_MODEL,
        embedding_dimension=EMBEDDING_DIMENSION,
    )
    run_vector_db_test(client, "LatencyTest", EMBEDDED_QUERY)


def test_qdrant_user_isolation():
    """A user_id-filtered search must never return another user's points.

    Mirrors the LongtermMemory read/write path — payload carries user_id and
    retrieve_query filters on it via the user_id tenant index.
    """
    client = QdrantClient(
        embedding_model=EMBEDDING_MODEL,
        embedding_dimension=EMBEDDING_DIMENSION,
    )
    collection = "UserIsolationTest"
    try:
        client.create_collection(collection, payload_indexes=["user_id"])
        # Two users, distinct summaries, same (irrelevant) embedding.
        client.push_documents(
            collection_name=collection,
            ids=["11111111-1111-1111-1111-111111111111",
                 "22222222-2222-2222-2222-222222222222"],
            queries=None,
            embedded_queries=[EMBEDDED_QUERY, EMBEDDED_QUERY],
            payloads=[
                {"query": "user A private memory", "user_id": "user-A"},
                {"query": "user B private memory", "user_id": "user-B"},
            ],
        )

        # A's query returns only A's memory.
        resp_a = client.retrieve_query(
            collection_name=collection,
            embedded_query=EMBEDDED_QUERY,
            user_id="user-A",
            with_payload=True,
        )
        payloads_a = [p.payload["user_id"] for p in resp_a.points]
        assert payloads_a, "expected A's own memory to be returned"
        assert all(uid == "user-A" for uid in payloads_a), \
            f"cross-tenant leak: A's query returned {payloads_a}"

        # An unfiltered read still sees both — proves the isolation is the filter,
        # not an artifact of empty data.
        resp_all = client.retrieve_query(
            collection_name=collection,
            embedded_query=EMBEDDED_QUERY,
            with_payload=True,
        )
        assert len(resp_all.points) == 2
    finally:
        client.delete_collection(collection)
