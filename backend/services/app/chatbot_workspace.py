from langchain_core.messages import AIMessage, HumanMessage, AnyMessage
from typing import Any, Dict, List, Optional, Tuple

import json, re

from logger import get_logger
from services.chatbot.constants.schemas import GraphState, UserInfo, UserDocumentRef
from services.utils.pattern_cipher import PatternCipher
from services.utils.mongo_client import get_mongo_client
from services.utils.redis_client import get_redis_client
from vector_database_tests.utils.qdrant_client import get_qdrant_client
from services.documents_upload.constants import (
    USER_DOCUMENTS_COLLECTION, 
    EMBEDDING_MODEL, 
    EMBEDDING_DIMENSION,
)
from services.utils.pattern_cipher import get_pattern_cipher

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
        self.__mongo_client = get_mongo_client()
        self.__redis_client = get_redis_client()
        self.__qdrant_client = get_qdrant_client(EMBEDDING_MODEL, EMBEDDING_DIMENSION)
        self.__pattern_cipher = get_pattern_cipher()
        self.__graph = graph

        # Start the service
        self.__redis_client.start_expire_listener(self._on_redis_expired)

    def close(self) -> None:
        self.__mongo_client.close()
        self.__redis_client.close()

    # ========== DOCUMENT UPLOAD (1 doc per user) ==========
    # Ownership is enforced by the user_id key inside each Mongo query. The worker
    # updates status directly; the API goes through these. Vector chunks live in
    # Qdrant (UserDocuments). Uploads use replace semantics — see prepare_replace.
    def get_user_document(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self.__mongo_client.get_user_document(user_id)

    def new_document_id(self, user_id: str) -> str:
        return self.__pattern_cipher.hash_user_id(user_id)

    def prepare_new_document(
            self,
            user_id: str,
            doc_id: str,
            filename: str,
            mime: str,
            size: int,
            description: str,
        ) -> None:
        """
        Atomic replace: purge the user's existing document (Qdrant chunks + Mongo
        metadata) before inserting the new one, upholding the 1-doc-per-user rule.
        The unique index on user_id is the last-resort guard.
        """
        existing = self.__mongo_client.get_user_document(user_id)
        if existing:
            self.delete_user_document(user_id, existing["doc_id"])
        self.__mongo_client.create_document(user_id, doc_id, filename, mime, size, description)

    def delete_user_document(self, user_id: str, doc_id: str) -> None:
        """Purge the doc's Qdrant chunks (user_id-scoped) then its Mongo metadata."""
        self.__qdrant_client.delete_by_doc(USER_DOCUMENTS_COLLECTION, user_id, doc_id)
        self.__mongo_client.delete_document(user_id, doc_id)

    # ========== DATA CACHING KEYS ==========
    @staticmethod
    def _sidebar_cache_key(id: str) -> str:
        """Cached sidebar chat list (id/name rows) for one user; TTL-refreshed from Mongo."""
        return f"sidebar:{id}"

    @staticmethod
    def _pending_rename_key(user_id: str) -> str:
        """Map of chat_id -> new name awaiting flush to Mongo; merged into the sidebar on read."""
        return f"pending_rename:{user_id}"

    @staticmethod
    def _user_cache_key(email: str) -> str:
        """Cached user_info (id/username/password) keyed by email; speeds up login."""
        return f"userinfo:{email}"

    @staticmethod
    def _messages_cache_key(chat_id: str) -> str:
        """Legacy per-chat messages cache key (superseded by _messages_snap_list_key)."""
        return f"messages:{chat_id}"

    @staticmethod
    def _messages_snap_list_key(chat_id: str) -> str:
        """Redis list of two JSON blobs: [chat_history rows], [shortterm_memory]."""
        return f"msgsnap:{chat_id}"

    # ========== DUMMY FLUSHING KEYS ==========
    # Empty (value "1") TTL trigger, never read. When it expires Redis fires an
    # 'expired' event that tells us to write the cached chat snapshot (msgsnap)
    # back to Mongo. Write-behind: many edits in the TTL window = one Mongo write.
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
        """
        Called by the Redis expire listener whenever any key expires. If the expired
        key is a flush trigger, write the matching cached data back to Mongo — this is
        how Redis "notifies" us to persist the cache. Non-flush keys are ignored.
        """
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
        cached = self.__redis_client.get_json_key(_WORKSPACE_COLLECTION, cache_key)
        if isinstance(cached, dict):
            if cached.get("password") != password:
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
            {
                "user_id": doc["user_id"],
                "username": doc["username"],
                "password": doc["password"],
            },
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

    def delete_chat(self, id: str, chat_id: str) -> None:
        if not self._user_owns_chat_cached(id, chat_id):
            raise PermissionError("Chat not found for user")

        self.__mongo_client.delete_chat(chat_id, id)

        # Drop cached chat state + sidebar so it disappears immediately
        self.__redis_client.list_delete(
            _WORKSPACE_COLLECTION, self._messages_snap_list_key(chat_id)
        )
        self.__redis_client.delete_key(
            _WORKSPACE_COLLECTION, self._flush_chat_key(chat_id)
        )
        self.__redis_client.delete_key(
            _WORKSPACE_COLLECTION, self._sidebar_cache_key(id)
        )

        # Drop any pending rename for this chat so it can't resurrect the deleted
        # chat's name in the sidebar or get flushed to a now-missing Mongo doc.
        pending = self._load_pending_renames(id)
        if pending.pop(str(chat_id), None) is not None:
            self.__redis_client.set_key(
                _WORKSPACE_COLLECTION,
                self._pending_rename_key(id),
                pending,
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
        state = self._build_reply_state(id, username, chat_id, message)
        result = graph.invoke(
            input = state,
            config = {"configurable": {"thread_id": chat_id}},
        )

        chatbot_response = self._extract_reply(result)
        self._persist_reply(chat_id, result)
        return chatbot_response

    def _build_reply_state(
            self,
            id: str,
            username: str,
            chat_id: str,
            message: str,
        ) -> GraphState:
        """
        Validate ownership and build the GraphState fed to the graph.
        Shared by the blocking and streaming reply paths.
        """
        if not self._user_owns_chat_cached(id, chat_id):
            raise PermissionError("User does not own this chat")

        doc = self._load_chat_doc(chat_id)
        if doc is None:
            raise PermissionError("Chat not found")

        chat_history: List[Dict[str, Any]] = list(doc.get("chat_history") or [])
        chat_history.append({"role": "user", "content": message})
        chat_history_lc = self._rows_to_lc_messages(chat_history)

        user_info = UserInfo(
            user_id = id,
            username = username,
            email = "",
            password = "",
            plan = "Free",
        )

        # Attach the user's uploaded doc. Only a fully-ingested ('ready') doc is 
        # advertised, so a doc mid-ingestion isn't offered as answerable.
        user_document = None
        user_doc = self.__mongo_client.get_user_document(id)
        if user_doc and user_doc.get("status") == "ready":
            user_document = UserDocumentRef(
                doc_id = user_doc.get("doc_id", ""),
                description = user_doc.get("description", ""),
            )

        return GraphState(
            user_info = user_info,
            chat_history = chat_history_lc,
            shortterm_memory = doc["shortterm_memory"],
            user_document = user_document,
        )

    @staticmethod
    def _extract_reply(result) -> str:
        if not result.get("chat_history"):
            raise ValueError("Empty chat history")

        ai_response = result["chat_history"][-1]
        if isinstance(ai_response, AIMessage):
            return ai_response.content
        return "Sorry, I didn't understand that."

    def _persist_reply(self, chat_id: str, result) -> None:
        rows = self._lc_messages_to_rows(result["chat_history"])
        stm_out = result.get("shortterm_memory") or []

        self._write_chat_snap(chat_id, rows, stm_out)
        self.__redis_client.set_key(
            _WORKSPACE_COLLECTION,
            self._flush_chat_key(chat_id),
            "1",
            _TTL_SECONDS,
        )

    def stream_reply(
            self,
            id: str,
            username: str,
            chat_id: str,
            message: str,
            graph,
        ) -> str:
        return self.append_user_and_reply(id, username, chat_id, message, graph)

    @staticmethod
    def chunk_reply(reply: str):
        """
        Split a completed reply into word-ish chunks for token streaming.

        Trailing whitespace stays with each token so a client can naively
        concatenate chunks and reproduce the original text.
        """
        return re.findall(r"\S+\s*|\s+", reply)