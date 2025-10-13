from cryptography.fernet import Fernet
from pymongo import MongoClient
from pymongo.server_api import ServerApi
import os

from dotenv import load_dotenv
load_dotenv()

# Generate key (store it securely, not hardcoded)
# key = os.getenv("CRYPTOGRAPHY_KEY")#Fernet.generate_key()
# print("Key:", key.decode())

# f = Fernet(os.getenv("CRYPTOGRAPHY_KEY"))

# print (key)
# # Encrypt
# user_name = "Tan Hoang Khoa Nguyen"
# encoded_str = f.encrypt(user_name.encode()).decode()  # convert bytes -> UTF-8 string
# print("Encrypted for MongoDB:", encoded_str)

# # ===== Store `encoded_str` in MongoDB =====
# # MongoDB can safely store this as a normal string
# client = MongoClient(os.getenv("MONGODB_URI"), server_api = ServerApi('1'))
# database = client["lawAdvisory"]
# collection = database["chat_pool"]
# collection.insert_one({
#     "conversation_id": "1002060406",
#     "message": encoded_str})

# # ===== Retrieve from MongoDB =====
# ricky = collection.find_one({"conversation_id": "05bb3f54-6c3a-549d-ad9e-6b06fa8b5963"})
# nhinhi = ricky["message"]
# retrieved = nhinhi.encode()  # convert back to bytes
# decrypted = f.decrypt(retrieved).decode()
# print("Decrypted:", decrypted)

k = []
n = [0, 1, 7, 10]
k += n
k += n
print (k)