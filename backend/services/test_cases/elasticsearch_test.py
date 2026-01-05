import sys, time
from elasticsearch import Elasticsearch

INDEX = "elasticsearch_service_test"
ELASTICSEARCH_URL = "http://la-elasticsearch:9200"

class ElasticSearchTest:
    def __init__(self):
        self.es = Elasticsearch(ELASTICSEARCH_URL, verify_certs = False)

    def create(self):
        sentences = [
            "I like playing football on weekends.",
            "Python is my favorite programming language.",
            "My cat loves chasing laser pointers.",
            "I enjoy long walks on the beach at sunrise."
        ]
        
        if self.es.indices.exists(index = INDEX):
            self.es.indices.delete(index = INDEX)

        self.es.indices.create(
            index = INDEX,
            body = {
                "mappings": {
                    "properties": {
                        "text": {"type": "text"}
                    }
                }
            }
        )

        for i, text in enumerate(sentences):
            self.es.index(index = INDEX, id = i, document = {"text": text})
        
        self.es.indices.refresh(index = INDEX)
        print("Indexed sentences!")

    def keyword_search(self, query):
        resp = self.es.search(
            index = INDEX,
            body = {
                "query": {
                    "match": {
                        "text": query
                    }
                }
            }
        )
        return resp

if __name__ == "__main__":
    user_input = input("Type 'Execute' to run: ")
    if user_input != "Execute":
        sys.exit()

    start_time = time.time()
    elasticsearchtest = ElasticSearchTest()
    q = "Which programming language do I enjoy?"

    elasticsearchtest.create()
    resp = elasticsearchtest.keyword_search(q)
    
    print("Query:", q)
    if resp["hits"]["hits"]:
        print("Top match:")
        hit = resp["hits"]["hits"][0]
        print(f"id = {hit['_id']}, score = {hit['_score']}, text = {hit['_source']['text']}")
    else:
        print("No matches found.")
    print ("Execution time: ", time.time() - start_time, "seconds")