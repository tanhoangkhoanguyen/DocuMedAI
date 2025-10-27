import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from constants.schemas import ChatSession


class ChatPool:
    def __init__(self, chat_pool_path: str):
        self.__chat_pool_path = chat_pool_path
        self.__metadata_path = os.path.join(self.__chat_pool_path, 'metadata.json')
        self.__metadata = {}
        self.reload_metadata()
    
    def __bootstrap_metadata(self):
        self.__metadata = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_updated_at": datetime.now(timezone.utc).isoformat(),
            "chat_dict": {}
        }
    
    def __load_metadata(self):
        with open(self.__metadata_path, "r") as f:
            self.__metadata = json.load(f)

    def reload_metadata(self):
        if not os.path.exists(self.__metadata_path):
            self.__bootstrap_metadata()
        else:
            self.__load_metadata()
    
    def __update_metadata(self, chat_session: Optional[ChatSession] = None):
        self.__metadata["last_updated_at"] = datetime.now(timezone.utc).isoformat()
        if chat_session is not None:
            self.__metadata["chat_dict"][str(chat_session.chat_id)] = chat_session.chat_name
    
    def __save_metadata(self):
        with open(self.__metadata_path, "w") as f:
            json.dump(self.__metadata, f, indent=4)

    def create_new_chat_session(self) -> ChatSession:
        new_chat_id = str(uuid.uuid4())
        while new_chat_id in self.__metadata["chat_dict"].keys():
            print(f'[INFO CHATPOOL] Chat session {new_chat_id} already exists')
            new_chat_id = str(uuid.uuid4())
        chat_session = ChatSession(
            chat_id=new_chat_id
        )
        self.__update_metadata(chat_session)
        return chat_session
    
    def load_chat_session(self, chat_id: str) -> ChatSession:
        load_path = os.path.join(self.__chat_pool_path, f"{chat_id}.json")
        if not os.path.exists(load_path):
            print (f"""
                [ERROR] [backend.services.caching.core.controller.database] Chat session '{chat_id}' does not exist
            """)
            return None
        with open(load_path, "r") as f:
            chat_session = json.load(f)
        return ChatSession.model_validate(chat_session)
    
    def save_chat_session(self, chat_session: ChatSession):
        if chat_session is None or chat_session.chat_id is None:
            print(f'[ERROR CHATPOOL] Chat session is None or chat_id is None')
            return

        save_path = os.path.join(self.__chat_pool_path, f"{chat_session.chat_id}.json")
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(chat_session.model_dump_json(indent=4))
        print(f"[INFO CHATPOOL] Saved chat session to {save_path}")

        self.__metadata["chat_dict"][chat_session.chat_id] = chat_session.chat_name
        print(f"[INFO CHATPOOL] Saved chat session to {save_path}")
        self.__update_metadata(chat_session)
        
    
    def close(self):
        self.__save_metadata()
        print(f"[INFO CHATPOOL] Closed chat pool")
    
        
class Database:
    def __init__(self, root_path: str = "db"):
        self.__root_path = root_path
        self.__chat_pool_path = os.path.join(self.__root_path, "chat_pool")
        self.__chat_pool = ChatPool(self.__chat_pool_path)
        print(f"[INFO DATABASE] Finish initializing database")
    
    def __load_chat_pool(self):
        self.__chat_pool.reload_metadata()
        print(f"[INFO DATABASE] Loaded chat pool from {self.__chat_pool_path}")
    
    def save_chat_session(self, chat_session: ChatSession):
        self.__chat_pool.save_chat_session(chat_session)
        print(f"[INFO DATABASE] Saved chat session to {self.__chat_pool_path}")
    
    def load_chat_session(self, chat_id: str) -> ChatSession:
        chat_session = self.__chat_pool.load_chat_session(chat_id)
        if chat_session is None:
            print (f"""
                [INFO] [backend.services.caching.core.controller.database] Chat session '{chat_id}' is empty
            """)
        return chat_session
    
    def create_new_chat_session(self) -> ChatSession:
        new_chat_session = self.__chat_pool.create_new_chat_session()
        self.save_chat_session(new_chat_session)
        return new_chat_session
    
    def close(self):
        self.__chat_pool.close()
        print(f"[DATABASE] Closed database")


def mock_chat_data(num_chats: int = 20):
    database = Database()
    for i in range(num_chats):
        chat_session = database.create_new_chat_session()
        database.save_chat_session(chat_session)
    
    database.close()


if __name__ == "__main__":
    mock_chat_data()
    # new_chat_id = str(uuid.uuid4())
    # print(new_chat_id)
    # print(type(new_chat_id))
    # print(str(new_chat_id))
    # print(type(new_chat_id))


    