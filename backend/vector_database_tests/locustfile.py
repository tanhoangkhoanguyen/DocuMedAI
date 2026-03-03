from locust import HttpUser, task, between

class RetrievalUser(HttpUser):
    wait_time = between(1, 2)  # each user waits 1-2s between tasks

    @task
    def query_us_history(self):
        self.client.post("/retrieve", json={
            "query": "hello chatbot. Let me know about the United States history"
        })

    @task
    def query_chatbot(self):
        self.client.post("/retrieve", json={
            "query": "what is a chatbot?"
        })

    @task
    def query_general(self):
        self.client.post("/retrieve", json={
            "query": "tell me something interesting"
        })