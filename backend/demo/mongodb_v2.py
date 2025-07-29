
from dotenv import load_dotenv
import os
from pydantic import BaseModel
from pymongo import MongoClient

uri = os.getenv("MONGODB_URI")
client = MongoClient(uri)

user_demo_db = client["user_demo_db"]

user_info = user_demo_db["user_info"]

