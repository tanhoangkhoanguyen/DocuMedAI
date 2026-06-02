from pymongo import MongoClient as PyMongoClient
from typing import Any, Dict, Optional

from logger import get_logger


MONGO_URL = "mongodb://la-mongo:27017/"
_HOST_DB = "host"
_LOGGER = get_logger(
    name = "Mongo_tool",
    level = "INFO",
)


class MongoClient:
    """
    Refer to schemas.py for or default collection names
    """
    def __init__(self) -> None:
        self.__client = PyMongoClient(MONGO_URL)
        if not self.ping():
            raise RuntimeError(f"Cannot connect to Mongo at {MONGO_URL}")

    def ping(self) -> bool:
        try:
            self.__client.admin.command("ping")
            return True
        except Exception as e:
            _LOGGER.error(f"Mongo ping failed\n\t{str(e)}")
            return False

    def _db(self):
        return self.__client[_HOST_DB]

    def create_account(
            self,
            user_id: str,
            username: str,
            email: str,
            password: str,
            sub: str,
            plan: str = "Free",
        ) -> None:
        try:
            self._db()["user_info"].insert_one({
                "user_id": user_id,
                "username": username,
                "email": email,
                "password": password,
                "plan": plan,
                "sub": sub,
            })
            self._db()["user_data"].insert_one({
                "user_id": user_id,
                "chat_ids": [],
            })
        except Exception as e:
            _LOGGER.error(f"create_account failed for user_id={user_id}\n\t{str(e)}")
            raise

    def insert_chat(self, chat_id: str, chat_name: str, user_id: str) -> None:
        try:
            self._db()["user_chat"].insert_one({
                "chat_id": chat_id,
                "chat_name": chat_name,
                "chat_history": [],
                "shortterm_memory": [],
            })
            self._db()["user_data"].update_one(
                {"user_id": user_id},
                {
                    "$push": {
                        "chat_ids": chat_id,
                    },
                    "$setOnInsert": {"user_id": user_id},
                },
                upsert=True,
            )
        except Exception as e:
            _LOGGER.error(f"insert_chat failed chat_id={chat_id}\n\t{str(e)}")
            raise

    def user_exists(self, collection_name: str, key: str, value: str) -> bool:
        try:
            return self._db()[collection_name].count_documents(
                {key: value},
                limit=1,
            ) > 0
        except Exception as e:
            _LOGGER.error(f"user_exists failed {key}={value}\n\t{str(e)}")
            return False

    def ensure_account(
            self,
            user_id: str,
            username: str,
            email: str,
            password: str,
            sub: str,
            plan: str = "Free",
        ) -> None:
        try:
            self._db()["user_info"].update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "username": username,
                        "email": email,
                        "sub": sub,
                        "plan": plan,
                    },
                    "$setOnInsert": {
                        "user_id": user_id,
                        "password": password,
                    },
                },
                upsert = True,
            )
            self._db()["user_data"].update_one(
                {"user_id": user_id},
                {"$setOnInsert": {"user_id": user_id, "chat_ids": []}},
                upsert=True,
            )
        except Exception as e:
            _LOGGER.error(f"ensure_account failed user_id={user_id}\n\t{str(e)}")
            raise

    def rename_chat(self, chat_id: str, chat_name: str) -> None:
        try:
            self._db()["user_chat"].update_one(
                {"chat_id": chat_id},
                {"$set": {"chat_name": chat_name}},
            )
        except Exception as e:
            _LOGGER.error(f"rename_chat failed chat_id={chat_id}\n\t{str(e)}")
            raise

    def get_one(self, collection_name: str, key: str, value: str) -> Optional[Dict[str, Any]]:
        try:
            return self._db()[collection_name].find_one({key: value})
        except Exception as e:
            _LOGGER.error(
                f"get_one failed collection={collection_name} {key}={value}\n\t{str(e)}"
            )
            return None
        
    def user_owns_chat(self, user_id: str, chat_id: str) -> bool:
        user_data = self.get_one(
            collection_name = "user_data",
            key = "user_id",
            value = user_id,
        )
        if not user_data:
            return False
        ids = user_data.get("chat_ids")
        if not isinstance(ids, list):
            return False
        return chat_id in ids

    def update_user_chat_state(
            self,
            chat_id: str,
            chat_history: Any,
            shortterm_memory: Any,
        ) -> None:
        try:
            self._db()["user_chat"].update_one(
                {"chat_id": chat_id},
                {
                    "$set": {
                        "chat_history": chat_history,
                        "shortterm_memory": shortterm_memory,
                    },
                },
            )
        except Exception as e:
            _LOGGER.error(f"update_user_chat_state failed chat_id={chat_id}\n\t{str(e)}")
            raise

    def close(self) -> None:
        try:
            self.__client.close()
        except Exception as e:
            _LOGGER.error(f"Failed to close Mongo client\n\t{str(e)}")