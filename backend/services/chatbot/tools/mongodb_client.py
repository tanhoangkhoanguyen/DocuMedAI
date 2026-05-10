from services.chatbot.core.constants.schemas import UserInfo, UserData, UserChat

import os, sys, uuid, bcrypt, pytz
from dotenv import load_dotenv
load_dotenv()
from pymongo import MongoClient
from datetime import datetime

MONGODB_URL = "mongodb://la-mongodb:27017/"

class MongoDBSetup:
    def __init__(self):
        self.__client = MongoClient(MONGODB_URL)
        self.__uuid_namespace = uuid.UUID(os.getenv("UUID_NAMESPACE"))

    def __test_connection(self):
        try:
            self.__client.admin.command('ping')
            print(f"""
                [INFO] [backend.data_setup.mongodb_setup] Connected to MongoDB
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.mongodb_setup] Failed to connect to MongoDB:
                \t{str(e)}
            """)

    def __hash_password(self, password:str) -> str:
        salt = bcrypt.gensalt()
        hashed_password = bcrypt.hashpw(password = password.encode('utf-8'), salt = salt)
        return hashed_password.decode('utf-8')

    def __create_admin(self):
        self.__client.drop_database("host")
        database = self.__client["host"]

        user_name = "Admin"
        hashed_user_name = uuid.uuid5(self.__uuid_namespace, user_name)
        password = "Admin123()"
        hashed_password = self.__hash_password(password)
        user_id = user_name + str(datetime.now(pytz.utc))
        hashed_user_id = uuid.uuid5(self.__uuid_namespace, user_id)
        plan = "admin"
        hashed_plan = uuid.uuid5(self.__uuid_namespace, plan)

        collection = database["user_info"]
        admin_info = UserInfo(
            user_id = str(hashed_user_id),
            user_name = str(hashed_user_name),
            password = str(hashed_password),
            plan = str(hashed_plan)
        )
        collection.insert_one(admin_info.__dict__)

        chat_id = user_name + str(datetime.now(pytz.utc))
        hashed_chat_id = uuid.uuid5(self.__uuid_namespace, chat_id)

        collection = database["user_data"]
        admin_data = UserData(
            user_id = str(hashed_user_id),
            chat_id = [str(hashed_chat_id)]
        )
        collection.insert_one(admin_data.__dict__)

        collection = database["chat_pool"]
        admin_chat = UserChat(
            chat_id = str(hashed_chat_id),
            chat_name = "1st_conversation",
            chat_history = [
                {"role": "user", "content": "Hello, I am admin!"},
                {"role": "chatbot", "content": "Hi admin."}
            ]
        )
        collection.insert_one(admin_chat.__dict__)

    def execute(self):
        self.__test_connection()
        self.__create_admin()        
        self.__client.close()