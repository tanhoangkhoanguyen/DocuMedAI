import os
from pymongo import MongoClient
from pprint import pprint

from dotenv import load_dotenv
load_dotenv()  


def mongodb_demo():
    print("===== DEMO MONGODB =====")
    # 2.1 Build the MongoDB URI and connect
    uri = os.getenv("MONGODB_URI")
    client = MongoClient(uri)

    # 2.2 Select a database & collection
    db         = client["demo_db"]
    collection = db["people"]

    # 2.3 CREATE: insert a document
    alice = {"name": "Alice", "age": 30, "city": "Hanoi"}
    result = collection.insert_one(alice)
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
    delete_result = collection.delete_one({"name": "Alice"})
    print(f"Deleted count: {delete_result.deleted_count}\n")

    # 2.7 CLEANUP: close the connection
    client.close()
    print("Connection closed.")
    print("===== DEMO MONGODB DONE =====")


if __name__ == "__main__":
    mongodb_demo()
    