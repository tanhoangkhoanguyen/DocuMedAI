# demo/elastic_demo.py

import os
from dotenv import load_dotenv
from elasticsearch import Elasticsearch, AuthorizationException, TransportError

# ── 1. Load configuration ─────────────────────────────────────────────────
load_dotenv()  # expects .env next to this script

def elasticsearch_demo():
    print("===== DEMO ELASTICSEARCH =====")

    api_key = os.getenv("ELASTIC_API_KEY", "")
    if ":" not in api_key:
        raise RuntimeError("ELASTIC_API_KEY must be in the form <id>:<secret>")

    key_id, key_secret = api_key.split(":", 1)
    es_host = os.getenv("ELASTIC_HOST", "http://la-elasticsearch:9200")

    # ── 2. Instantiate the client ───────────────────────────────────────────────
    es = Elasticsearch(es_host, api_key=(key_id, key_secret))


    def print_resp(title, resp):
        print(f"\n{title}: ")
        print(resp)


    # ── 3. Helper to run an operation with auth error handling ───────────────────
    def run_op(op_fn, title):
        try:
            result = op_fn()
            print_resp(title, result)
        except AuthorizationException as auth_err:
            print(f"\n!!! AUTHORIZATION FAILED for {title} !!!\n{auth_err}\n")
        except TransportError as te:
            print(f"\n!!! TRANSPORT ERROR for {title} !!!\n{te}\n")


    # ── 4. Demo sequence ─────────────────────────────────────────────────────────
    # 4.1 Health check
    run_op(lambda: es.cluster.health(), "Cluster Health")

    # 4.2 Create index (will succeed only if API key has create_index privilege)
    mapping = {
        "mappings": {
            "properties": {
                "msg": {"type": "text"},
                "time": {"type": "date"}
            }
        }
    }
    run_op(lambda: es.indices.create(index="demo-index", body=mapping), "Create Index")

    # 4.3 Index a document
    doc = {"msg": "Hello from Python", "time": "2025-07-25T18:00:00"}
    run_op(lambda: es.index(index="demo-index", id=1, document=doc), "Index Document")

    # 4.4 Refresh & search
    run_op(lambda: es.indices.refresh(index="demo-index"), "Refresh Index")
    run_op(lambda: es.search(index="demo-index", body={"query": {"match": {"msg": "Python"}}}), 
        "Search Hits")

    # 4.5 Cleanup: delete index (requires delete_index)
    run_op(lambda: es.indices.delete(index="demo-index"), "Delete Index")

    print("===== DEMO ELASTICSEARCH DONE =====")

if __name__ == "__main__":
    elasticsearch_demo()