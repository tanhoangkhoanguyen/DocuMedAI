import uuid, pytest
from utils.redis_client import RedisClient


pytestmark = pytest.mark.integration


@pytest.fixture
def redis_client():
    client = RedisClient()
    yield client
    client.close()


def test_redis_ping(redis_client):
    assert redis_client.ping() is True


def test_set_get_and_delete_key(redis_client):
    collection = "123456"
    key = "sample-key"
    redis_client.create_collection(collection)
    redis_client.set_key(collection, key, {"hello": "world"})
    raw = redis_client.get_json_key(collection, key)
    assert raw == {"hello": "world"}
    redis_client.delete_key(collection, key)
    assert redis_client.get_key(collection, key) is None
    redis_client.delete_collection(collection)


def test_list_push_and_range(redis_client):
    collection = "123456"
    list_name = "sample-list"
    redis_client.create_collection(collection)
    redis_client.list_push(collection, list_name, ["a", "b"])
    values = redis_client.list_range(collection, list_name, 0, -1)
    assert values == ["a", "b"]
    redis_client.delete_collection(collection)
