import sys, time
from pymongo import MongoClient

MONGODB_URL = "mongodb://la-mongodb:27017/"

class MongoDBTest:
    def __init__(self):
        client = MongoClient(MONGODB_URL)
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
        print("Retrieving sentences from MongoDB:")
        for doc in self.collection.find():
            print(f"id = {doc['_id']}, text = {doc['text']}")

if __name__ == "__main__":
    user_input = input("Type 'Execute' to run: ")
    if user_input != "Execute":
        sys.exit()
    
    start_time = time.time()
    mongodbtest = MongoDBTest()
    mongodbtest.create()
    mongodbtest.list_all()
    print ("Execution time: ", time.time() - start_time, "seconds")