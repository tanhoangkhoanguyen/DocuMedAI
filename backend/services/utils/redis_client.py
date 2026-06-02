from redis import Redis
from typing import Any, Callable, Dict, List, Optional, Sequence

import json, threading, time, warnings
warnings.filterwarnings("ignore")

from logger import get_logger


_LOGGER = get_logger(
    name = "Redis_tool",
    level = "INFO",
)
REDIS_URL = "redis://la-redis:6379/0"
DEFAULT_KEY_PREFIX = "documedai"
_COLLECTION_MARKER = "__marker"


class RedisClient:
    def __init__(self) -> None:
        self.__client = Redis.from_url(REDIS_URL, decode_responses = True)
        if not self.ping():
            raise RuntimeError(f"Cannot connect to Redis at {REDIS_URL}")

        self.__expire_thread: Optional[threading.Thread] = None
        self.__expire_stop = threading.Event()                                       # Guarantees visibility across threads
        self.__pubsub_client: Optional[Redis] = None

    def ping(self) -> bool:
        try:
            return bool(self.__client.ping())
        except Exception as e:
            _LOGGER.error(f"Redis ping failed\n\t{str(e)}")
            return False

    def start_expire_listener(self, handler: Callable[[str], None]) -> None:
        """
        Call once at app startup; handler receives the full Redis key that expired.
        """
        if self.__expire_thread is not None and self.__expire_thread.is_alive():
            return

        if self.__pubsub_client is None:
            self.__pubsub_client = Redis.from_url(
                REDIS_URL,
                decode_responses = True,
                socket_timeout = None,
            )

        def run() -> None:
            while not self.__expire_stop.is_set():
                pubsub = None
                try:
                    pubsub = self.__pubsub_client.pubsub()
                    pubsub.psubscribe("__keyevent@*__:expired")
                    for msg in pubsub.listen():
                        if self.__expire_stop.is_set():
                            break
                        if msg.get("type") != "pmessage":
                            continue
                        key = msg.get("data")
                        if isinstance(key, str):
                            handler(key)
                except Exception as e:
                    if not self.__expire_stop.is_set():
                        _LOGGER.warning(
                            f"Redis expire listener reconnecting after error: {e}"
                        )
                finally:
                    if pubsub is not None:
                        try:
                            pubsub.close()
                        except Exception:
                            pass
                if not self.__expire_stop.is_set():
                    time.sleep(1)

        self.__expire_thread = threading.Thread(target = run, daemon = True)
        self.__expire_thread.start()

    def stop_expire_listener(self) -> None:
        self.__expire_stop.set()

    @staticmethod
    def _marker_key(collection_name: str) -> str:    
        """
        Return collection key
        """
        return f"{DEFAULT_KEY_PREFIX}:c:{collection_name}:{_COLLECTION_MARKER}"

    @staticmethod
    def _string_key(collection_name: str, key: str) -> str:
        return f"{DEFAULT_KEY_PREFIX}:c:{collection_name}:s:{key}"

    @staticmethod
    def _list_key(collection_name: str, list_name: str) -> str:
        return f"{DEFAULT_KEY_PREFIX}:c:{collection_name}:l:{list_name}"

    def collection_exists(self, collection_name: str) -> bool:
        try:
            return self.__client.exists(self._marker_key(collection_name))
        except Exception as e:
            _LOGGER.error(f"Failed to check collection '{collection_name}'\n\t{str(e)}")
            return False

    def delete_collection(self, collection_name: str) -> int:
        pattern = f"{DEFAULT_KEY_PREFIX}:c:{collection_name}:*"
        try:
            num_keys = 0
            for redis_key in self.__client.scan_iter(match = pattern, count = 1000):
                self.__client.delete(redis_key)
                num_keys += 1
            _LOGGER.info(f"Deleted collection '{collection_name}'")
            return num_keys
        except Exception as e:
            _LOGGER.error(f"Failed to delete collection '{collection_name}'\n\t{str(e)}")
            raise

    def create_collection(
            self,
            collection_name: str,
            ttl_seconds: Optional[int] = None,
        ) -> None:
        marker = self._marker_key(collection_name)
        try:
            self.delete_collection(collection_name)
            if ttl_seconds is not None:
                self.__client.set(marker, "1", ex = ttl_seconds, nx = True)
            else: # permanent object
                self.__client.set(marker, "1", nx = True)
            _LOGGER.info(f"Created collection marker '{collection_name}'")
        except Exception as e:
            _LOGGER.error(f"Failed to create collection '{collection_name}'\n\t{str(e)}")
            raise

    def __encode(self, obj: Any) -> str:
        if hasattr(obj, "model_dump"):  # Pydantic model case
            obj = obj.model_dump()
        if isinstance(obj, tuple):
            obj = list(obj)
        if not isinstance(obj, (dict, list)):
            raise TypeError(
                f"Expected dict, list, tuple, or object with model_dump(), got {type(obj)!r}"
            )
        return json.dumps(obj, ensure_ascii = False, sort_keys = True)

    def __decode(self, s: str) -> Optional[Dict[str, Any]]:
        if s is None or s == "":
            return None
        text = s.strip()
        if text.startswith(("{", "[")):
            try:
                out = json.loads(text)
                return out if isinstance(out, (dict, list)) else None
            except json.JSONDecodeError:
                pass
        return None

    def __normalize_value(self, value: Any) -> str:
        if isinstance(value, (dict, list, tuple)) or hasattr(value, "model_dump"):
            return self.__encode(value)
        return value if isinstance(value, str) else str(value)

    def set_key(
            self,
            collection_name: str,
            key: str,
            value: Any,
            ttl_seconds: Optional[int] = None,
            only_if_not_exists: bool = False,                                    # Normal cache overwrite on every write
        ) -> None:
        rkey = self.__string_key(collection_name, key)
        try:
            payload = self.__normalize_value(value)
            if ttl_seconds is not None:
                self.__client.set(
                    rkey,
                    payload,                                                                        # Has to be str
                    ex = ttl_seconds,
                    nx = only_if_not_exists,                                                        # True <-> Uniqueness
                )
            else: # permanent object case
                self.__client.set(
                    rkey,
                    payload,
                    nx = only_if_not_exists,
                )
        except Exception as e:
            _LOGGER.error(f"Failed to set key '{key}' in collection '{collection_name}'\n\t{str(e)}")

    def get_key(self, collection_name: str, key: str):
        try:
            return self.__client.get(self.__string_key(collection_name, key))
        except Exception as e:
            _LOGGER.error(f"Failed to get key '{key}' from collection '{collection_name}'\n\t{str(e)}")
            return None

    def get_json_key(self, collection_name: str, key: str) -> Any:
        raw = self.get_key(collection_name, key)
        if raw is None:
            return None
        return self.__decode(raw)

    def delete_key(self, collection_name: str, key: str) -> int:
        try:
            num_keys = self.__client.delete(self.__string_key(collection_name, key))
            return num_keys
        except Exception as e:
            _LOGGER.error(f"Failed to delete key '{key}' in collection '{collection_name}'\n\t{str(e)}")
            raise

    def key_exists(self, collection_name: str, key: str) -> bool:
        try:
            return self.__client.exists(self.__string_key(collection_name, key))
        except Exception as e:
            _LOGGER.error(f"Failed exists check for '{key}' in '{collection_name}'\n\t{str(e)}")
            return False

    def expire_key(
            self,
            collection_name: str,
            key: str,
            ttl_seconds: int,
        ) -> bool:
        try:
            return bool(
                self.__client.expire(
                    self.__string_key(collection_name, key),
                    ttl_seconds,
                )
            )
        except Exception as e:
            _LOGGER.error(f"Failed to set TTL on '{key}' in '{collection_name}'\n\t{str(e)}")
            raise

    def get_key_ttl(self, collection_name: str, key: str) -> int:
        """
        Remaining TTL in seconds,
        - n seconds left
        - -1 if key exists but has no TTL
        - -2 if missing.
        """
        try:
            return self.__client.ttl(self.__string_key(collection_name, key))
        except Exception as e:
            _LOGGER.error(f"Failed to read TTL for '{key}' in '{collection_name}'\n\t{str(e)}")
            return -2

    def list_push(
            self,
            collection_name: str,
            list_name: str,
            values: Optional[Sequence[Any]] = None,
            ttl_seconds: Optional[int] = None,
            side: str = "right",
        ) -> None:
        """
        Push one element, or many in a single round trip.
        Examples:
            list_push("chat", "room1", "Hello")
            list_push("chat", "room1", "How are you?")
        Redis stores:
            "chat":"room1" -> ["Hello", "How are you?"]
        """
        rkey = self.__list_key(collection_name, list_name)
        try:
            payloads = [self.__normalize_value(v) for v in values]
            if not payloads:
                return
            if side == "left":
                self.__client.lpush(rkey, *payloads)
            else:
                self.__client.rpush(rkey, *payloads)
            if ttl_seconds is not None:
                self.__client.expire(rkey, int(ttl_seconds))
        except Exception as e:
            _LOGGER.error(f"Failed list_push '{list_name}' in '{collection_name}'\n\t{str(e)}")

    def list_delete(self, collection_name: str, list_name: str) -> int:
        try:
            return int(self.__client.delete(self.__list_key(collection_name, list_name)))
        except Exception as e:
            _LOGGER.error(f"Failed list_delete '{list_name}' in '{collection_name}'\n\t{str(e)}")
            raise

    def list_range( # or list get
            self,
            collection_name: str,
            list_name: str,
            start: int = 0,
            end: int = -1,
        ):
        try:
            out = self.__client.lrange(self.__list_key(collection_name, list_name), start, end)
            return list(out) if out else []
        except Exception as e:
            _LOGGER.error(f"Failed list_range '{list_name}' in '{collection_name}'\n\t{str(e)}")
            return None

    def list_pop_left(self, collection_name: str, list_name: str):
        """
        Removes and returns only the LEFTMOST single element
        """
        try:
            return self.__client.lpop(self.__list_key(collection_name, list_name))
        except Exception as e:
            _LOGGER.error(f"Failed list_pop_left '{list_name}' in '{collection_name}'\n\t{str(e)}")
            return None

    def list_length(self, collection_name: str, list_name: str):
        try:
            return int(self.__client.llen(self.__list_key(collection_name, list_name)))
        except Exception as e:
            _LOGGER.error(f"Failed list_length '{list_name}' in '{collection_name}'\n\t{str(e)}")
            return 0

    def list_get_json(
            self,
            collection_name: str,
            list_name: str,
            start: int = 0,
            end: int = -1,
        ) -> List[Dict[str, Any]]:
        rows = self.list_range(collection_name, list_name, start, end)
        if rows is None:
            return []
        out: List[Dict[str, Any]] = []
        for raw in rows:
            if raw is None:
                continue
            decoded = self.__decode(raw)
            if decoded is not None:
                out.append(decoded)
        return out

    def close(self) -> None:
        self.stop_expire_listener()
        if self.__expire_thread is not None:
            self.__expire_thread.join(timeout = 2)
        if self.__pubsub_client is not None:
            try:
                self.__pubsub_client.close()
            except Exception as e:
                _LOGGER.error(f"Failed to close Redis pubsub client\n\t{str(e)}")
        try:
            self.__client.close()
        except Exception as e:
            _LOGGER.error(f"Failed to close Redis client\n\t{str(e)}")