import os, uuid, bcrypt, pytz
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime

SHARED_PATTERN_CIPHER = None

class PatternCipher:
    def __init__(self):
        raw_uuid = os.getenv("UUID_NAMESPACE")
        if not raw_uuid:
            raise ValueError("UUID_NAMESPACE is not configured")
        self.__namespace = uuid.UUID(raw_uuid)

    def hash_username(self, plain: str) -> str:
        return str(uuid.uuid5(self.__namespace, plain))

    def hash_password(self, plain: str) -> str:
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(plain.encode("utf-8"), salt=salt)
        return hashed.decode("utf-8")

    def hash_user_id(self, username: str) -> str:
        composite = username + str(datetime.now(pytz.utc))
        return str(uuid.uuid5(self.__namespace, composite))

    def stable_local_user_id(self, email: str) -> str:
        return str(uuid.uuid5(self.__namespace, "local:" + email.strip().lower()))

    def verify_password(self, plain: str, hashed: str) -> bool:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

def get_pattern_cipher():
    global SHARED_PATTERN_CIPHER
    if SHARED_PATTERN_CIPHER is None:
        SHARED_PATTERN_CIPHER = PatternCipher()
    return SHARED_PATTERN_CIPHER