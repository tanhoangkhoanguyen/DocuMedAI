from services.caching.core.constants.schemas import ChatRequest, ChatSession, Message
from services.caching.core.controller.database import Database

import asyncio, json, orjson, uuid
from typing import AsyncGenerator, Callable, Optional, Dict, Any, Tuple, List
from anyio import to_thread
from redis.asyncio import Redis

# -------------------- SYSTEM CACHE -------------------- #
class SystemCache:
    def __init__(self, r: Redis, db: Database, ttl_seconds: int = settings.session_ttl_seconds):
        self.__r = r
        self.__db = db
        self.__ttl_seconds = ttl_seconds

    @staticmethod
    def _k(chat_id: str) -> str:
        return f"chat:{chat_id}"

    @staticmethod
    def _km(chat_id: str) -> str:
        return f"chat:{chat_id}:messages"

    async def get_or_load(self, chat_id: str) -> ChatSession:
        key = self._k(chat_id)

        raw = await self.__r.get(key)
        if raw:
            try:
                return ChatSession.model_validate(orjson.loads(raw))
            except Exception as e:
                print (f"""
                    [ERROR] [backend.services.caching.core.controller.system] Failed to run SystemCache.get_or_load(). Attempting to migrate
                    \t{str(e)}
                """, flushing = True)
                data = orjson.loads(raw)
                if isinstance(data, dict) and "chat_name" in data and "chat_names" not in data:
                    v = data.get("chat_name")
                    data["chat_names"] = [v] if isinstance(v, str) else (v if isinstance(v, list) else None)
                    data.pop("chat_name", None)
                sess = ChatSession.model_validate(data)
                await self.__r.set(key, orjson.dumps(sess.model_dump()), ex = self.__ttl_seconds)
                return sess

        sess = await to_thread.run_sync(self.__db.load_chat_session, chat_id)
        if sess is None:
            sess = ChatSession(chat_id = chat_id, messages = [])

        await self.__r.set(key, orjson.dumps(sess.model_dump()), ex = self.__ttl_seconds)

        if sess.messages:
            pipe = self.__r.pipeline(transaction = True)
            pipe.delete(self._km(chat_id))
            pipe.rpush(self._km(chat_id), *[orjson.dumps(m.model_dump()) for m in sess.messages])
            pipe.expire(self._km(chat_id), self.__ttl_seconds)
            await pipe.execute()

        return sess

    async def touch(self, chat_id: str) -> None:
        await self.__r.expire(self._k(chat_id), self.__ttl_seconds)
        await self.__r.expire(self._km(chat_id), self.__ttl_seconds)

    async def append_message(self, chat_id: str, message: Message) -> None:
        pipe = self.__r.pipeline(transaction = True)
        pipe.rpush(self._km(chat_id), orjson.dumps(message.model_dump()))
        pipe.expire(self._km(chat_id), self.__ttl_seconds)
        pipe.expire(self._k(chat_id), self.__ttl_seconds)
        await pipe.execute()

    async def set_session_metadata(self, session: ChatSession) -> None:
        await self.__r.set(self._k(session.chat_id), orjson.dumps(session.model_dump()), ex=self.__ttl_seconds)
        await self.__r.expire(self._km(session.chat_id), self.__ttl_seconds)

    async def snapshot(self, chat_id: str) -> Optional[ChatSession]:
        root = await self.__r.get(self._k(chat_id))
        if not root:
            return None
        sess = ChatSession.model_validate(orjson.loads(root))
        msgs_raw = await self.__r.lrange(self._km(chat_id), 0, -1)
        msgs = [Message.model_validate(orjson.loads(m)) for m in msgs_raw]
        sess.messages = msgs
        return sess

    async def flush_to_db(self, chat_id: str) -> bool:
        sess = await self.snapshot(chat_id)
        if sess is None:
            return False
        await to_thread.run_sync(self.__db.save_chat_session, sess)
        await self.__r.delete(self._k(chat_id), self._km(chat_id))
        return True

    async def sweep_all(self, near_expiry_threshold: int = 30) -> int:
        flushed = 0
        cursor = 0
        while True:
            cursor, keys = await self.__r.scan(cursor=cursor, match="chat:*", count=500)
            str_keys = [k.decode() if isinstance(k, bytes) else k for k in keys]
            roots = [k for k in str_keys if not k.endswith(":messages")]
            if not roots:
                if cursor == 0:
                    break
                continue
            pipe = self.__r.pipeline()
            for k in roots:
                pipe.ttl(k)
            ttls = await pipe.execute()
            for k, ttl in zip(roots, ttls):
                if isinstance(ttl, int) and 0 <= ttl <= near_expiry_threshold:
                    chat_id = k.split("chat:", 1)[1]
                    if await self.flush_to_db(chat_id):
                        flushed += 1
            if cursor == 0:
                break
        return flushed

    async def cleanup_all_cache(self) -> int:
        """Delete ALL cache keys: chat:* and chat:*:messages."""
        deleted = 0
        cursor = 0
        batch: List[str] = []
        while True:
            cursor, keys = await self.__r.scan(cursor=cursor, match="chat:*", count=1000)
            for k in keys:
                k = k.decode() if isinstance(k, bytes) else k
                batch.append(k)
                if len(batch) >= 1000:
                    deleted += await self.__r.delete(*batch)
                    batch.clear()
            if cursor == 0:
                break
        if batch:
            deleted += await self.__r.delete(*batch)
        return deleted


# -------------------- REQUEST QUEUE -------------------- #
class RequestQueue:
    def __init__(self, r: Redis, stream_name: str = "chat:requests", group_name: str = "chat-consumers") -> None:
        self.r = r
        self.stream = stream_name
        self.group = group_name

    @staticmethod
    def channel_for(request_id: str) -> str:
        return f"chat:resp:{request_id}"

    async def ensure_group(self) -> None:
        try:
            await self.r.xgroup_create(
                name = self.stream, 
                groupname = self.group, 
                id = "0-0", 
                mkstream = True
            )
        except Exception:
            pass  # already exists

    async def enqueue(self, req:ChatRequest) -> str:
        request_id = getattr(req, "request_id", None)
        if not request_id:
            request_id = str(uuid.uuid4())
            try:
                object.__setattr__(req, "request_id", request_id)
            except Exception:
                pass
        payload = orjson.loads(req.model_dump_json())
        payload.setdefault("request_id", request_id)
        await self.r.xadd(self.stream, {"data": orjson.dumps(payload)})
        return request_id

    async def read_batch(self, consumer_name:str, count:int = 1, block_ms:int = 1000):
        resp = await self.r.xreadgroup(
            groupname = self.group,
            consumername = consumer_name,
            streams = {self.stream: ">"},
            count = count,
            block = block_ms,
        )
        out = []
        for _, entries in resp or []:
            for entry_id, fields in entries:
                payload = orjson.loads(fields[b"data"])
                out.append((entry_id, payload))
        return out

    async def ack(self, entry_id: str) -> None:
        await self.r.xack(self.stream, self.group, entry_id)

    async def publish(self, request_id: str, data: str | bytes) -> None:
        if isinstance(data, str):
            data = data.encode()
        await self.r.publish(self.channel_for(request_id), data)

    async def sse_stream(self, request_id: str, heartbeat: int = 15):
        chan = self.channel_for(request_id)
        ps = self.r.pubsub()
        await ps.subscribe(chan)
        try:
            ticks = 0
            while True:
                msg = await ps.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg and msg["type"] == "message":
                    data: bytes = msg["data"]
                    yield b"data: " + data + b"\n\n"
                    if data == b"[DONE]":
                        break
                ticks += 1
                if ticks >= heartbeat:
                    ticks = 0
                    yield b": keep-alive\n\n"
        finally:
            await ps.unsubscribe(chan)
            await ps.close()

    async def cleanup_stream(self) -> None:
        """Destroy consumer group (if exists) and delete the stream key entirely."""
        # Try to destroy group first (in case DEL fails to remove PEL state)
        try:
            await self.r.xgroup_destroy(self.stream, self.group)
        except Exception:
            pass
        # Delete the stream
        try:
            await self.r.delete(self.stream)
        except Exception:
            pass


# -------------------- SYSTEM -------------------- #
class System:
    def __init__(self,
                 redis_url: Optional[str] = None,
                 request_stream: str = "chat:requests",
                 request_group: str = "chat-consumers",
                 consumer_name: str = "worker-1",
                 db: Optional[Database] = None,
                 ttl_seconds: Optional[int] = None) -> None:
        redis_url = redis_url or f"redis://:{settings.redis_password}@la-redis:{settings.redis_port}/{settings.redis_db}"
        self.__r = Redis.from_url(redis_url, decode_responses=False)
        self.db = db or Database()
        self.cache = SystemCache(self.__r, self.db, ttl_seconds or settings.session_ttl_seconds)
        self.queue = RequestQueue(self.__r, request_stream, request_group)
        self.consumer_name = consumer_name
        self._stop = asyncio.Event()

    async def stop(self):
        """Signal background tasks to stop and clean up Redis resources."""
        self._stop.set()
        # cleanup queue & cache before closing redis
        await self.queue.cleanup_stream()
        await self.cache.cleanup_all_cache()
        await self.__r.aclose()

    async def enqueue_and_stream(self, req: ChatRequest) -> Tuple[str, AsyncGenerator[bytes, None]]:
        await self.queue.ensure_group()

        request_id = getattr(req, "request_id", None) or str(uuid.uuid4())
        try:
            object.__setattr__(req, "request_id", request_id)
        except Exception:
            pass

        gen = self.queue.sse_stream(request_id)  # subscribe first
        await self.queue.enqueue(req)            # then enqueue
        return request_id, gen

    async def _handle(
            self, 
            payload:Dict[str, Any],
            generate_tokens:Callable[[Dict[str, Any]], AsyncGenerator[str, None]]
        ) -> None:
        chat_id = payload["chat_id"]
        request_id = payload["request_id"]
        user_msg = Message.model_validate(payload["message"])

        _ = await self.cache.get_or_load(chat_id)
        await self.cache.append_message(chat_id, user_msg)

        full: List[str] = []
        try:
            async for tok in generate_tokens(payload):
                if tok:
                    full.append(tok)
                    await self.queue.publish(request_id, tok)
        except Exception as e:
            err = {"type": "error", "message": str(e)}
            await self.queue.publish(request_id, json.dumps(err))
        finally:
            if full:
                assistant = Message(role="assistant", content="".join(full))
                await self.cache.append_message(chat_id, assistant)
            await self.queue.publish(request_id, "[DONE]")
            await self.cache.touch(chat_id)

    async def worker_loop(
            self,
            token_gen:Callable[[Dict[str, Any]], AsyncGenerator[str, None]],
            block_ms:int = 1000
        ) -> None:
        await self.queue.ensure_group()
        try:
            while not self._stop.is_set():
                try:
                    batch = await self.queue.read_batch(
                        self.consumer_name, 
                        count = 1,
                        block_ms = block_ms
                    )
                except asyncio.CancelledError:
                    print (f"""
                        [SYSTEM] [backend.services.caching.core.controller.system] Cancelled System.worker_loop().queue.read_batch
                    """, flush = True)
                    break
                except Exception as e:
                    print (f"""
                        [ERROR] [backend.services.caching.core.controller.system] Failed to run System.worker_loop().queue.read_batch
                        \t{str(e)}
                    """, flush = True)
                    await asyncio.sleep(0.1)
                    continue

                if not batch:
                    continue

                for entry_id, payload in batch:
                    try:
                        await self._handle(payload, token_gen)
                    except asyncio.CancelledError:
                        print (f"""
                            [SYSTEM] [backend.services.caching.core.controller.system] Cancelled System.worker_loop()._handle
                        """, flush = True)
                        raise
                    except Exception as e:
                        print (f"""
                            [ERROR] [backend.services.caching.core.controller.system] Failed to run System.worker_loop()._handle
                            \t{str(e)}
                        """, flush = True)
                    finally:
                        try:
                            await self.queue.ack(entry_id)
                        except Exception as e:
                            print (f"""
                                [ERROR] [backend.services.caching.core.controller.system] Failed to run System.worker_loop().ack
                                \t{str(e)}
                            """, flush = True)
        finally:
            print (f"""
                [INFO] [backend.services.caching.core.controller.system] Exited System.worker_loop()
            """, flush = True)

    async def close(self) -> None:
        await self.__r.aclose()