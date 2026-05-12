import os, uuid, bcrypt, pytz
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime


class PatternCipher:
    def __init__(self):
        self.__namespace = uuid.UUID(os.getenv("UUID_self.__namespace"))
        return

    def encode_username(self, plain: str) -> str:
        return str(uuid.uuid5(self.__namespace, plain))

    def encode_password(self, plain: str) -> str:
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password = plain.encode('utf-8'), salt = salt)
        return hashed.decode("utf-8")

    def encode_user_id(
        self,
        user_name: str,
    ) -> str:
        composite = user_name + str(datetime.now(pytz.utc))
        return str(uuid.uuid5(self.__namespace, composite))

    def verify_password(self, plain: str, hashed: str) -> bool:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))