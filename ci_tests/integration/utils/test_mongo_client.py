import uuid, pytest
from utils.mongo_client import MongoClient


pytestmark = pytest.mark.integration


@pytest.fixture
def mongo_client():
    client = MongoClient()
    yield client
    client.close()


def test_mongo_ping(mongo_client):
    assert mongo_client.ping() is True


def test_create_account_and_get_one(mongo_client):
    user_id = "123456"
    email = "test@example.com"
    mongo_client.create_account(
        user_id=user_id,
        username="test",
        email=email,
        password="123456",
        sub="",
    )
    doc = mongo_client.get_one("user_info", "user_id", user_id)
    assert doc is not None
    assert doc["email"] == email


def test_insert_chat_and_user_owns_chat(mongo_client):
    user_id = "123456"
    chat_id = "123456"
    mongo_client.create_account(
        user_id=user_id,
        username="test",
        email="test@example.com",
        password="123456",
        sub="",
    )
    mongo_client.insert_chat(chat_id, "My Chat", user_id)
    assert mongo_client.user_owns_chat(user_id, chat_id) is True

    mongo_client.rename_chat(chat_id, "Renamed chat")
    doc = mongo_client.get_one("user_chat", "chat_id", chat_id)
    assert doc["chat_name"] == "Renamed chat"
