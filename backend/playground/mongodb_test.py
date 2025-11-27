import sys
from pymongo import MongoClient

class MongoDBTest:
    def __init__(self):
        client = MongoClient("mongodb://la-mongodb:27017/")
        db = client["testdb"]
        self.collection = db["sentences"]

    def create(self):
        sentences = [
            "I like playing football on weekends.",
            "Python is my favorite programming language.",
            "My cat loves chasing laser pointers.",
            "I enjoy long walks on the beach at sunrise."
        ]
        self.collection.delete_many({})

        for i, text in enumerate(sentences):
            self.collection.insert_one({"_id": i, "text": text})
        print("Sentences uploaded!")

    def list_all(self):
        print("\nRetrieving sentences from MongoDB:")
        for doc in self.collection.find():
            print(f"id={doc['_id']} text={doc['text']}")

user_input = input()
if user_input != "Execute":
    sys.exit()

mongodb = MongoDBTest()
# mongodb.create()
mongodb.list_all()