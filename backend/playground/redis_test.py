import redis

r = redis.Redis(host='la-redis', port=6379, db=0, decode_responses=True)

# 2️⃣ Sentences to store
sentences = [
    "I like playing football on weekends.",
    "Python is my favorite programming language.",
    "My cat loves chasing laser pointers.",
    "I enjoy long walks on the beach at sunrise."
]

r.flushdb()

for sentence in sentences:
    r.rpush("sentences_list", sentence)

print("Sentences uploaded to Redis!")

retrieved = r.lrange("sentences_list", 0, -1)
print("\nRetrieved sentences from Redis:")
for i, sentence in enumerate(retrieved):
    print(f"{i}: {sentence}")
