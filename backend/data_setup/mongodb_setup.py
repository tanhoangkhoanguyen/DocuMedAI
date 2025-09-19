import os, uuid, bcrypt, pytz
from pymongo import MongoClient
from pymongo.server_api import ServerApi
from datetime import datetime

class MongoDBSetup:
    def __init__(self):
        self.__uri = os.getenv("MONGODB_URI")
        self.__client = MongoClient(self.__uri, server_api = ServerApi('1'))
        try:
            self.__client.admin.command('ping')
            print("Successfully connected to MongoDB!")
        except Exception as e:
            print(f"MongoDB setup error: {e}")
        
        self.__namespace = uuid.UUID(os.getenv("UUID_NAMESPACE"))

    def __hash_func(self, input):
        salt = bcrypt.gensalt()
        hashed_input = bcrypt.hashpw(password = input.encode('utf-8'), salt = salt)
        return hashed_input.decode('utf-8')
    
    def __compare_hash(self, input_1, input_2):
        return bcrypt.checkpw(input_1.encode('utf-8'), input_2.encode('utf-8'))
    
    def __hash_compare(self, input, hashed):
        return bcrypt.checkpw(input.encode('utf-8'), hashed)

    def __update_account(self, collection, user_id, key, modified_value):
        collection.update_one(
            {"user_id": user_id},
            {"$set": {key: modified_value}}
        )

    def __delete_account(collection, user_id):
        collection.delete_one({"user_id": user_id})

    def mongo_setup(self):
        database = self.__client["lawAdvisory"]

        user_name = "Admin"
        hashed_user_name = uuid.uuid5(self.__namespace, user_name)
        password = "Admin123()"
        hashed_password = self.__hash_func(password)
        user_id = user_name + str(datetime.now(pytz.utc))
        hashed_user_id = uuid.uuid5(self.__namespace, user_id)
        plan = "admin"
        hashed_plan = uuid.uuid5(self.__namespace, plan)

        # Add user_info
        collection = database["user_info"]
        admin_info = {
            "user_id": str(hashed_user_id),
            "user_name": str(hashed_user_name),
            "password": str(hashed_password),
            "plan": str(hashed_plan)
        }
        collection.insert_one(admin_info)

        chat_id = user_name + str(datetime.now(pytz.utc))
        hash_chat_id = uuid.uuid5(self.__namespace, chat_id)

        # Add user_data
        collection = database["user_data"]
        admin_data = {
            "user_id": str(hashed_user_id),
            "chat_id": [str(hash_chat_id)]
        }
        collection.insert_one(admin_data)

        # Add chat_pool
        collection = database["chat_pool"]
        admin_chat = {
            "chat_id": str(hash_chat_id),
            "chat_name": "1st_conversation",
            "chat_history": [
                {"role": "user", "content": "Hello, I am admin!"},
                {"role": "chatbot", "content": "Hi admin."}
            ]
        }
        collection.insert_one(admin_chat)
        
        self.__client.close()