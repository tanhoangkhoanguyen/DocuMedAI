from datetime import datetime, timezone
from pymongo import MongoClient as PyMongoClient
from typing import Any, Dict, Optional

from logger import get_logger


MONGO_URL = "mongodb://la-mongo:27017/"
SHARED_MONGO_CLIENT = None
_HOST_DB = "documedai_prod"
_LOGGER = get_logger(
    name = "Mongo_client",
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
        try:
            self._ensure_documents_indexes()
        except Exception as e:
            _LOGGER.error(f"ensure documents indexes failed\n\t{str(e)}")

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

    def delete_chat(self, chat_id: str, user_id: str) -> None:
        try:
            self._db()["user_chat"].delete_one({"chat_id": chat_id})
            self._db()["user_data"].update_one(
                {"user_id": user_id},
                {"$pull": {"chat_ids": chat_id}},
            )
        except Exception as e:
            _LOGGER.error(f"delete_chat failed chat_id={chat_id}\n\t{str(e)}")
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

    # ==================== Document upload metadata ====================
    # Collection "documents" tracks each user's uploaded file and its ingestion
    # status. The vector chunks live in Qdrant (UserDocuments); this is the
    # metadata / job-status store. Product rule: ONE document per user — a unique
    # index on user_id enforces it at the storage layer (uploads use replace
    # semantics: the old doc is deleted before the new one is inserted).
    # user_id in every query = ownership.

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_documents_indexes(self) -> None:
        # Unique on user_id => the 1-doc-per-user rule can't be violated even under
        # a race; a stale duplicate insert fails loudly instead of silently.
        self._db()["documents"].create_index("user_id", unique = True)
        self._db()["documents"].create_index("doc_id")

    def create_document(
            self,
            user_id: str,
            doc_id: str,
            filename: str,
            mime: str,
            size: int,
            description: str,
        ) -> None:
        try:
            now = self._now()
            self._db()["documents"].insert_one({
                "doc_id": doc_id,
                "user_id": user_id,
                "filename": filename,
                "mime": mime,
                "size": size,
                "description": description,                                             # user-supplied; drives tool routing
                "status": "queued",
                "chunk_count": 0,
                "error": None,
                "created_at": now,
                "updated_at": now,
            })
        except Exception as e:
            _LOGGER.error(f"create_document failed doc_id={doc_id}\n\t{str(e)}")
            raise

    def set_document_status(
            self,
            doc_id: str,
            status: str,
            chunk_count: Optional[int] = None,
            error: Optional[str] = None,
        ) -> None:
        try:
            update: Dict[str, Any] = {"status": status, "updated_at": self._now()}
            if chunk_count is not None:
                update["chunk_count"] = chunk_count
            update["error"] = error                                                     # cleared on success, set on failure
            self._db()["documents"].update_one(
                {"doc_id": doc_id},
                {"$set": update},
            )
        except Exception as e:
            _LOGGER.error(f"set_document_status failed doc_id={doc_id}\n\t{str(e)}")
            raise

    def get_user_document(self, user_id: str) -> Optional[Dict[str, Any]]:
        """The user's single document (1-doc rule), or None. Keyed by user_id."""
        try:
            return self._db()["documents"].find_one(
                {"user_id": user_id},
                {"_id": 0},
            )
        except Exception as e:
            _LOGGER.error(f"get_user_document failed user_id={user_id}\n\t{str(e)}")
            return None

    def delete_document(self, user_id: str, doc_id: str) -> None:
        try:
            self._db()["documents"].delete_one({"doc_id": doc_id, "user_id": user_id})  # Extra safety
        except Exception as e:
            _LOGGER.error(f"delete_document failed doc_id={doc_id}\n\t{str(e)}")
            raise

    def close(self) -> None:
        try:
            self.__client.close()
        except Exception as e:
            _LOGGER.error(f"Failed to close Mongo client\n\t{str(e)}")


def get_mongo_client():
    global SHARED_MONGO_CLIENT
    if SHARED_MONGO_CLIENT is None:
        SHARED_MONGO_CLIENT = MongoClient()
    return SHARED_MONGO_CLIENT