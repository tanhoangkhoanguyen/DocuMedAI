from langchain_core.messages import AIMessage, HumanMessage, AnyMessage
from typing import Any, Dict, List, Optional, Tuple

import json, re

from logger import get_logger
from services.chatbot.constants.schemas import GraphState, UserInfo
from services.chatbot.tools.pattern_cipher import PatternCipher
from services.utils.mongo_client import MongoClient
from services.utils.redis_client import RedisClient


_SHARED_MONGO_CLIENT = MongoClient()
_SHARED_REDIS_CLIENT = RedisClient()
_SHARED_PATTERN_CIPHER = PatternCipher()
_LOGGER = get_logger(
    name = "chatbot_workspace",
    level = "INFO"
)

_WORKSPACE_COLLECTION = "workspace"
_FLUSH_CHAT_PREFIX = "flush:chatdoc:"
_FLUSH_SIDEBAR_PREFIX = "flush:sidebar:"
_TTL_SECONDS = 1800


class ChatbotWorkspace:
    def __init__(self, graph) -> None:
        # Initialize objects
        self.__mongo_client = _SHARED_MONGO_CLIENT
        self.__redis_client = _SHARED_REDIS_CLIENT
        self.__pattern_cipher = _SHARED_PATTERN_CIPHER
        self.__graph = graph

        # Start the service
        self.__redis_client.start_expire_listener(self._on_redis_expired)

    def close(self) -> None:
        self.__mongo_client.close()
        self.__redis_client.close()

    @staticmethod
    def _sidebar_cache_key(id: str) -> str:
        return f"sidebar:{id}"

    @staticmethod
    def _pending_rename_key(user_id: str) -> str:
        return f"pending_rename:{user_id}"

    @staticmethod
    def _messages_cache_key(chat_id: str) -> str:
        return f"messages:{chat_id}"

    @staticmethod
    def _user_cache_key(email: str) -> str:
        return f"userinfo:{email}"

    @staticmethod
    def _messages_snap_list_key(chat_id: str) -> str:
        """
        Redis list of two JSON blobs: [chat_history rows], [shortterm_memory].
        """
        return f"msgsnap:{chat_id}"

    @staticmethod
    def _flush_chat_key(chat_id: str) -> str:
        return f"{_FLUSH_CHAT_PREFIX}{chat_id}"

    @staticmethod
    def _flush_sidebar_key(user_id: str) -> str:
        return f"{_FLUSH_SIDEBAR_PREFIX}{user_id}"

    @staticmethod
    def _extract_email_name(email: str) -> str:
        email = (email or "").strip()
        match = re.search(r"^([^@]+)@", email)
        if match:
            return match.group(1)
        return email or "unknown_user"

    def _flush_sidebar_and_rename_to_mongo(self, user_id: str) -> None:
        pending_key = self._pending_rename_key(user_id)
        pending = self.__redis_client.get_json_key(_WORKSPACE_COLLECTION, pending_key)
        if isinstance(pending, dict):
            for cid, nm in pending.items():
                if cid is None or nm is None:
                    continue
                try:
                    self.__mongo_client.rename_chat(str(cid), str(nm).strip() or "Untitled")
                except Exception as e:
                    _LOGGER.error(f"flush rename failed chat_id={cid}\n\t{e}")
            self.__redis_client.delete_key(_WORKSPACE_COLLECTION, pending_key)
        self.__redis_client.delete_key(_WORKSPACE_COLLECTION, self._sidebar_cache_key(user_id))

    def _flush_chat_to_mongo(self, chat_id: str) -> None:
        pair = self.__redis_client.list_range(
            _WORKSPACE_COLLECTION,
            self._messages_snap_list_key(chat_id),
            0,
            -1,
        )
        if not pair or len(pair) < 2:
            return
        try:
            rows = json.loads(pair[0])
            stm = json.loads(pair[1])
        except json.JSONDecodeError:
            return
        if not isinstance(rows, list):
            return
        if not isinstance(stm, list):
            stm = []
        try:
            self.__mongo_client.update_user_chat_state(chat_id, rows, stm)
        except Exception as e:
            _LOGGER.error(f"flush chat to mongo failed chat_id={chat_id}\n\t{e}")
            return
        self.__redis_client.list_delete(_WORKSPACE_COLLECTION, self._messages_snap_list_key(chat_id))

    def _on_redis_expired(self, redis_key: str) -> None:
        marker = f":s:{_FLUSH_SIDEBAR_PREFIX}"
        if marker in redis_key:
            user_id = redis_key.split(marker, 1)[-1]
            self._flush_sidebar_and_rename_to_mongo(user_id)
            return
        marker = f":s:{_FLUSH_CHAT_PREFIX}"
        if marker in redis_key:
            chat_id = redis_key.split(marker, 1)[-1]
            self._flush_chat_to_mongo(chat_id)

    def _fetch_chats_from_mongo(self, user_id: str) -> List[Dict[str, Any]]:
        user_data = self.__mongo_client.get_one(
            collection_name = "user_data",
            key = "user_id",
            value = user_id,
        )
        if not user_data:
            return []
        out: List[Dict[str, Any]] = []
        for cid in user_data.get("chat_ids") or []:
            doc = self.__mongo_client.get_one(
                collection_name = "user_chat",
                key = "chat_id",
                value = cid,
            )
            if doc is not None:
                out.append({
                    "chat_id": doc["chat_id"],
                    "chat_name": doc["chat_name"],
                })
        return out

    def _user_owns_chat_cached(self, user_id: str, chat_id: str) -> bool:
        sidebar = self.__redis_client.get_json_key(
            _WORKSPACE_COLLECTION,
            self._sidebar_cache_key(user_id),
        )
        if isinstance(sidebar, list):
            for row in sidebar:
                if isinstance(row, dict) and str(row.get("chat_id")) == str(chat_id):
                    return True
        return self.__mongo_client.user_owns_chat(user_id, chat_id)

    def _load_pending_renames(self, user_id: str) -> Dict[str, str]:
        raw = self.__redis_client.get_json_key(
            _WORKSPACE_COLLECTION,
            self._pending_rename_key(user_id),
        )
        if not isinstance(raw, dict):
            return {}
        return raw

    def _merge_pending_renames(
            self,
            user_id: str,
            rows: List[Dict[str, Any]],
        ) -> List[Dict[str, Any]]:
        pending = self._load_pending_renames(user_id)
        if not pending:
            return rows
        merged: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            cid = row.get("chat_id")
            if cid is None:
                continue
            new_row = dict(row)
            if cid in pending:
                new_row["chat_name"] = pending[cid]
            merged.append(new_row)
        return merged

    @staticmethod
    def _rows_to_lc_messages(chat_history: List[dict]) -> List[Any]:
        out: List[AnyMessage] = []
        for row in chat_history:
            role = row.get("role", "user")
            content = row.get("content", "")
            if role == "user":
                out.append(HumanMessage(content = content))
            else:
                out.append(AIMessage(content = content))
        return out

    @staticmethod
    def _lc_messages_to_rows(messages: List[Any]) -> List[Dict[str, str]]:
        rows: List[Dict[str, str]] = []
        for m in messages:
            if isinstance(m, HumanMessage):
                rows.append({"role": "user", "content": str(m.content)})
            elif isinstance(m, AIMessage):
                rows.append({"role": "chatbot", "content": str(m.content)})
        return rows

    def _write_chat_snap(self, chat_id: str, rows: List[Any], stm: List[Any]) -> None:
        snap_key = self._messages_snap_list_key(chat_id)
        self.__redis_client.list_delete(_WORKSPACE_COLLECTION, snap_key)
        self.__redis_client.list_push(
            collection_name = _WORKSPACE_COLLECTION,
            list_name = snap_key,
            values = [rows, stm],
            ttl_seconds = _TTL_SECONDS,
        )

    def _load_chat_doc(self, chat_id: str) -> Optional[Dict[str, Any]]:
        pair = self.__redis_client.list_range(
            _WORKSPACE_COLLECTION,
            self._messages_snap_list_key(chat_id),
            0,
            -1,
        )
        if pair and len(pair) >= 2:
            try:
                hist = json.loads(pair[0])
                stm = json.loads(pair[1])
                if isinstance(hist, list):
                    return {
                        "chat_id": chat_id,
                        "chat_history": hist,
                        "shortterm_memory": stm,
                    }
            except json.JSONDecodeError:
                pass

        doc = self.__mongo_client.get_one(
            collection_name = "user_chat",
            key = "chat_id",
            value = chat_id,
        )
        if doc is not None:
            self._write_chat_snap(
                chat_id,
                doc.get("chat_history") or [],
                doc.get("shortterm_memory") or [],
            )
        return doc

    def ensure_user(self, sub: str, email: str) -> str:
        user_id = self.__pattern_cipher.stable_supabase_user_id(sub)
        username = self._extract_email_name(email)
        self.__mongo_client.ensure_account(
            user_id = user_id,
            username = username,
            email = email.strip(),
            password = "",
            sub = sub,
            plan = "Free",
        )
        return user_id

    def register_user(self, email: str, password: str, sub: str) -> Tuple[str, str]:
        em = email.strip()
        if self.__mongo_client.user_exists(
                collection_name = "user_info",
                key = "email",
                value = em,
            ):
            raise ValueError("Email exists.")

        plain = self._extract_email_name(em)
        user_id = self.__pattern_cipher.stable_local_user_id(em)
        if self.__mongo_client.user_exists(
                collection_name = "user_info",
                key = "user_id",
                value = user_id,
            ):
            raise ValueError("Email exists.")
        self.__mongo_client.create_account(
            user_id = user_id,
            username = plain,
            email = em,
            password = password,
            sub = sub,
            plan = "Free",
        )
        return user_id, plain

    def verify_login(self, email: str, password: str) -> Tuple[str, str]:
        cache_key = self._user_cache_key(email)
        cached = self.__redis_client.get_key(_WORKSPACE_COLLECTION, cache_key)
        if cached is not None:
            if cached != password:
                raise ValueError("bad_credentials")
            return str(cached["user_id"]), str(cached["username"])

        doc = self.__mongo_client.get_one(
            collection_name = "user_info",
            key = "email",
            value = email,
        )
        if doc is None or not doc.get("password"):
            raise ValueError("bad_credentials")
        if password != doc["password"]:
            raise ValueError("bad_credentials")
        self.__redis_client.set_key(
            _WORKSPACE_COLLECTION,
            cache_key,
            doc["password"],
            _TTL_SECONDS,
        )
        return str(doc["user_id"]), str(doc["username"])

    def list_chats(self, id: str) -> List[Dict[str, Any]]:
        cache_key = self._sidebar_cache_key(id)
        cached = self.__redis_client.get_json_key(_WORKSPACE_COLLECTION, cache_key)
        if isinstance(cached, list):
            return self._merge_pending_renames(id, cached)

        out = self._fetch_chats_from_mongo(id)
        self.__redis_client.set_key(
            _WORKSPACE_COLLECTION,
            cache_key,
            out,
            _TTL_SECONDS,
        )
        return self._merge_pending_renames(id, out)

    def create_chat(self, user_id: str) -> Dict[str, str]:
        cache_key = self._sidebar_cache_key(user_id)
        chat_id = self.__pattern_cipher.hash_user_id(cache_key)
        chat_name = chat_id
        self.__mongo_client.insert_chat(chat_id, chat_name, user_id)
        self.__redis_client.delete_key(_WORKSPACE_COLLECTION, cache_key)
        return {
            "chat_id": chat_id,
            "chat_name": chat_name,
        }

    def rename_chat(self, id: str, chat_id: str, chat_name: str) -> None:
        if not self._user_owns_chat_cached(id, chat_id):
            raise PermissionError("Chat not found for user")

        chat_name = chat_name.strip() or "Untitled"
        pending = self._load_pending_renames(id)
        pending[str(chat_id)] = chat_name
        self.__redis_client.set_key(
            _WORKSPACE_COLLECTION,
            self._pending_rename_key(id),
            pending,
        )
        self.__redis_client.set_key(
            _WORKSPACE_COLLECTION,
            self._flush_sidebar_key(id),
            "1",
            _TTL_SECONDS,
        )

    def get_messages(self, id: str, chat_id: str) -> List[Dict[str, Any]]:
        if not self._user_owns_chat_cached(id, chat_id):
            raise PermissionError("User does not own this chat")

        doc = self._load_chat_doc(chat_id)
        if doc is None:
            raise PermissionError("Chat not found")
        return doc.get("chat_history") or []

    def append_user_and_reply(
            self,
            id: str,
            username: str,
            chat_id: str,
            message: str,
            graph,
        ) -> str:
        if not self._user_owns_chat_cached(id, chat_id):
            raise PermissionError("User does not own this chat")

        doc = self._load_chat_doc(chat_id)
        if doc is None:
            raise PermissionError("Chat not found")

        chat_history: List[Dict[str, Any]] = list(doc.get("chat_history") or [])
        chat_history.append({"role": "user", "content": message})
        chat_history_lc = self._rows_to_lc_messages(chat_history)
        stm_in = doc["shortterm_memory"]

        user_info = UserInfo(
            user_id = id,
            username = username,
            email = "",
            password = "",
            plain = "Free",
        )

        state = GraphState(
            user_info = user_info,
            chat_history = chat_history_lc,
            shortterm_memory = stm_in,
        )
        result = graph.invoke(
            input = state,
            config = {"configurable": {"thread_id": chat_id}},
        )

        if not result.get("chat_history"):
            raise ValueError("Empty chat history")

        ai_response = result["chat_history"][-1]
        if isinstance(ai_response, AIMessage):
            chatbot_response = ai_response.content
        else:
            chatbot_response = "Sorry, I didn't understand that."

        rows = self._lc_messages_to_rows(result["chat_history"])
        stm_out = result.get["shortterm_memory"]

        self._write_chat_snap(chat_id, rows, stm_out)
        self.__redis_client.set_key(
            _WORKSPACE_COLLECTION,
            self._flush_chat_key(chat_id),
            "1",
            _TTL_SECONDS,
        )
        return chatbot_response