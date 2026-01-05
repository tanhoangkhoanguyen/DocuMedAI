import redis, sys, time

class RedisTest:
    def __init__(self):
        self.r = redis.Redis(host = 'la-redis', port = 6379, db = 0, decode_responses = True)

    def create(self):
        sentences = [
            "I like playing football on weekends.",
            "Python is my favorite programming language.",
            "My cat loves chasing laser pointers.",
            "I enjoy long walks on the beach at sunrise."
        ]
        self.r.flushdb()

        for sentence in sentences:
            self.r.rpush("sentences_list", sentence)
        print("Sentences uploaded to Redis!")

    def list_all(self):
        retrieved = self.r.lrange("sentences_list", 0, -1)
        print("Retrieved sentences from Redis:")
        for i, sentence in enumerate(retrieved):
            print(f"{i}: {sentence}")

if __name__ == "__main__":
    user_input = input("Type 'Execute' to run: ")
    if user_input != "Execute":
        sys.exit()
    
    start_time = time.time()
    redistest = RedisTest()
    redistest.create()
    redistest.list_all()
    print ("Execution time: ", time.time() - start_time, "seconds")