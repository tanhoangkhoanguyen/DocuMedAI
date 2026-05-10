from redis import Redis
from typing import Any, Dict, List, Optional, Union

import Dict, json, re, warnings
warnings.filterwarnings("ignore")

from logger import get_logger

LOGGER = get_logger(
    name = "Redis_tool",
    level = "INFO",
)
REDIS_URL = "local://la-redis:6379/0"
DEFAULT_KEY_PREFIX = "documedai"
_COLLECTION_MARKER = "__marker"
_WIRE_PAIR_SEP = "\x1e"

class RedisClient:
    def __init__(self):
        self.__client = Redis.from_url(
            REDIS_URL,
            decode_responses = True,
        )
        if not self.ping():
            raise

    def ping(self) -> bool:
        try:
            return bool(self.__client.ping())
        except Exception as e:
            LOGGER.error(f"Redis ping failed\n\t{str(e)}")
            return False

    def __marker_key(
            self,
            collection_name: str,
        ) -> str:
        return f"{DEFAULT_KEY_PREFIX}:c:{collection_name}:{_COLLECTION_MARKER}"

    def __string_key(
            self,
            collection_name: str,
            key: str
        ) -> str:
        return f"{DEFAULT_KEY_PREFIX}:c:{collection_name}:s:{key}"

    def __list_key(
            self,
            collection_name: str,
            list_name: str
        ) -> str:
        return f"{DEFAULT_KEY_PREFIX}:c:{collection_name}:l:{list_name}"

    def collection_exists(
            self,
            collection_name: str,
        ) -> bool:
        try:
            return self.__client.exists(self.__marker_key(collection_name))
        except Exception as e:
            LOGGER.error(f"Failed to check collection '{collection_name}'\n\t{str(e)}")
            return False

    def delete_collection(
            self,
            collection_name: str,
        ) -> int:
        pattern = f"{DEFAULT_KEY_PREFIX}:c:{collection_name}:*"
        try:
            num_keys = 0
            for redis_key in self.__client.scan_iter(match = pattern, count = 1000):
                self.__client.delete(redis_key)
                num_keys += 1
            LOGGER.info(f"Deleted collection '{collection_name}'")
            return num_keys
        except Exception as e:
            LOGGER.error(f"Failed to delete collection '{collection_name}'\n\t{str(e)}")
            raise

    def create_collection(
            self,
            collection_name: str,
            ttl_seconds: Optional[int] = None,
        ) -> None:
        marker = self.__marker_key(collection_name)
        try:
            self.delete_collection(collection_name)
            if ttl_seconds is not None:
                self.__client.set(marker, "1", nx = True, ex = ttl_seconds)
            else: # permanent object
                self.__client.set(marker, "1", nx = True)
            LOGGER.info(f"Created collection marker '{collection_name}'")
        except Exception as e:
            LOGGER.error(f"Failed to create collection '{collection_name}'\n\t{str(e)}")
            raise

    def __encode_dict(self, obj: Any) -> str:
        if hasattr(obj, "model_dump"):
            d = obj.model_dump()
        elif isinstance(obj, dict):
            d = obj
        else:
            raise TypeError(f"Expected dict or object with model_dump(), got {type(obj)!r}")
        if len(d) == 1:
            k, v = next(iter(d.items()))
            if not isinstance(k, str):
                k_str = str(k)
            if isinstance(v, (dict, list)):
                v_str = json.dumps(v, ensure_ascii = False)
            else:
                v_str = str(v)
            return f"{k_str}{_WIRE_PAIR_SEP}{v_str}"
        return json.dumps(d, ensure_ascii = False, sort_keys = True)

    def __decode_dict(self, s: str) -> Optional[Dict[str, Any]]:
        if s is None or s == "":
            return None
        text = s.strip()
        if text.startswith("{"):
            try:
                out = json.loads(text)
                return out if isinstance(out, dict) else None
            except json.JSONDecodeError:
                pass
        m = re.match(rf"^(.+?){re.escape(_WIRE_PAIR_SEP)}(.*)$", text, re.DOTALL)
        if m:
            return {m.group(1): m.group(2)}
        return None

    def __normalize_value(self, value: Union[str, Dict[str, Any], Any]) -> str:
        if isinstance(value, dict) or hasattr(value, "model_dump"):
            return self.__encode_dict(value)
        return value if isinstance(value, str) else str(value)

    def set_key(
            self,
            collection_name: str,
            key: str,
            value: Union[str, Dict[str, Any], Any],
            ttl_seconds: Optional[int] = None,
            only_if_not_exists: bool = True,
        ) -> None:
        rkey = self.__string_key(collection_name, key)
        try:
            payload = self.__normalize_value(value)
            if ttl_seconds is not None:
                self.__client.set(
                    rkey,
                    payload,
                    ex = ttl_seconds,
                    nx = only_if_not_exists,
                )
            else: # permanent object
                self.__client.set(
                    rkey,
                    payload,
                    nx = only_if_not_exists,
                )
        except Exception as e:
            LOGGER.error(f"Failed to set key '{key}' in collection '{collection_name}'\n\t{str(e)}")

    def get_key(
            self,
            collection_name: str,
            key: str
        ):
        try:
            return self.__client.get(self.__string_key(collection_name, key))
        except Exception as e:
            LOGGER.error(f"Failed to get key '{key}' from collection '{collection_name}'\n\t{str(e)}")
            return None

    def delete_key(
            self,
            collection_name: str,
            key: str
        ) -> int:
        try:
            num_keys = self.__client.delete(self.__string_key(collection_name, key))
            return num_keys
        except Exception as e:
            LOGGER.error(f"Failed to delete key '{key}' in collection '{collection_name}'\n\t{str(e)}")
            raise

    def key_exists(
            self,
            collection_name: str,
            key: str
        ) -> bool:
        try:
            return self.__client.exists(self.__string_key(collection_name, key))
        except Exception as e:
            LOGGER.error(f"Failed exists check for '{key}' in '{collection_name}'\n\t{str(e)}")
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
            LOGGER.error(f"Failed to set TTL on '{key}' in '{collection_name}'\n\t{str(e)}")
            raise

    def get_json(
            self,
            collection_name: str,
            key: str,
        ) -> Optional[Dict[str, Any]]:
        raw = self.get_key(collection_name, key)
        if raw is None:
            return None
        return self.__decode_dict(raw)

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
            LOGGER.error(f"Failed to read TTL for '{key}' in '{collection_name}'\n\t{str(e)}")
            return -2

    def list_push(
            self,
            collection_name: str,
            list_name: str,
            value: Union[str, Dict[str, Any], Any],
            ttl_seconds: Optional[int] = None,
            side: str = "right",
        ) -> None:
        """
        Requests:
            list_push("chat", "room1", "Hello")
            list_push("chat", "room1", "How are you?")
        Redis stores:
            "chat":"room1" -> ["Hello", "How are you?"]
        """
        rkey = self.__list_key(collection_name, list_name)
        try:
            payload = self.__normalize_value(value)
            if side == "left":
                self.__client.lpush(rkey, payload)
            else:
                self.__client.rpush(rkey, payload)
            if ttl_seconds is not None:
                self.__client.expire(rkey, int(ttl_seconds))
        except Exception as e:
            LOGGER.error(f"Failed list_push '{list_name}' in '{collection_name}'\n\t{str(e)}")

    def list_range(
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
            LOGGER.error(f"Failed list_range '{list_name}' in '{collection_name}'\n\t{str(e)}")
            return []

    def list_pop_left(self, collection_name: str, list_name: str):
        try:
            return self.__client.lpop(self.__list_key(collection_name, list_name))
        except Exception as e:
            LOGGER.error(f"Failed list_pop_left '{list_name}' in '{collection_name}'\n\t{str(e)}")
            return None

    def list_length(self, collection_name: str, list_name: str):
        try:
            return int(self.__client.llen(self.__list_key(collection_name, list_name)))
        except Exception as e:
            LOGGER.error(f"Failed list_length '{list_name}' in '{collection_name}'\n\t{str(e)}")
            return 0

    def list_get_json(
            self,
            collection_name: str,
            list_name: str,
            start: int = 0,
            end: int = -1,
        ) -> List[Dict[str, Any]]:
        rows = self.list_range(collection_name, list_name, start, end)
        out: List[Dict[str, Any]] = []
        for raw in rows:
            if raw is None:
                continue
            decoded = self.__decode_dict(raw)
            if decoded is not None:
                out.append(decoded)
        return out

    def close(self) -> None:
        try:
            self.__client.close()
        except Exception as e:
            LOGGER.error(f"Failed to close Redis client\n\t{str(e)}")
