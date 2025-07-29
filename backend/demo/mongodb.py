import os
from pymongo import MongoClient
from pymongo.server_api import ServerApi

from pprint import pprint

from dotenv import load_dotenv
load_dotenv()  


def mongodb_demo():
    print("===== DEMO MONGODB =====")
    # 2.1 Build the MongoDB URI and connect
    uri = os.getenv("MONGODB_URI")
    client = MongoClient(uri, server_api=ServerApi('1'))

    # 2.2 Select a database & collection
    db         = client["client_data"]
    collection = db["people"]

    # 2.3 CREATE: insert a document
    alice = {"name": "Alice", "age": 30, "city": "Hanoi"}
    result = collection.insert_one(alice)
    print(f"Inserted document ID: {result.inserted_id}\n")

    Khoa = {"name": "Khoa", "age": 18, "city": "Tampa"}
    result = collection.insert_one(Khoa)
    print(f"Inserted document ID: {result.inserted_id}\n")
    
    Phuc = {"name": "Phuc", "age": 20, "city": "HCM"}
    result = collection.insert_one(Phuc)
    print(f"Inserted document ID: {result.inserted_id}\n")
    
    Bob = {"name": "Bob", "age": 90, "city": "Maxay"}
    result = collection.insert_one(Bob)
    print(f"Inserted document ID: {result.inserted_id}\n")
    
    Chitoge = {"name": "Chitoge", "age": 17, "city": "Bonyari"}
    result = collection.insert_one(Chitoge)
    print(f"Inserted document ID: {result.inserted_id}\n")

    # 2.4 READ: find all documents
    print("All documents in ‘people’ collection:")
    for doc in collection.find({}):
        pprint(doc)
    print()

    # 2.5 UPDATE: increment Alice’s age
    update_result = collection.update_one(
        {"name": "Alice"},
        {"$inc": {"age": 1}}
    )
    print(f"Modified count: {update_result.modified_count}\n")

    # 2.6 DELETE: remove Alice’s document
    delete_result = collection.delete_one({"name": "Khoa"})
    print(f"Deleted count: {delete_result.deleted_count}\n")

    # 2.7 CLEANUP: close the connection
    client.close()
    print("Connection closed.")
    print("===== DEMO MONGODB DONE =====")

def check_authentication():
    uri = "mongodb+srv://lawAdvisory:LawAdvisoryMongoDBPassword@cluster0.rcb50pi.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
    # Create a new client and connect to the server
    client = MongoClient(uri, server_api=ServerApi('1'))
    try:
        client.admin.command('ping')
        print("Pinged your deployment. You successfully connected to MongoDB!")
    except Exception as e:
        print(e) 


if __name__ == "__main__":
    # check_authentication()
    mongodb_demo()
    