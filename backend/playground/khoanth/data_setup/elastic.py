import os, sys, warnings, pickle, json
from elasticsearch import Elasticsearch, helpers
from datetime import datetime
from typing import List, Dict, Any, Optional

warnings.filterwarnings("ignore")
ELASTIC_HOST = os.getenv("ELASTIC_HOST")
ELASTIC_PASSWORD = os.getenv("ELASTIC_PASSWORD")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY")
CHUNK_SIZE = 400
client = Elasticsearch(
    [ELASTIC_HOST],
    basic_auth = ("elastic", ELASTIC_PASSWORD),
    verify_certs = False,
    ssl_show_warn = False
)
index_name = "law_test"
collections = ["civil_law", "criminal_law", "environmental_law", "international_law", "labor_and_employment_law"]

def connection_test():
    try:
        info = client.info()
        print("Connected to Elasticsearch:")
        print(f"Cluster: {info['cluster_name']}")
        print(f"Version: {info['version']['number']}")
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit()

def create_mapping():
    mappings = {
        "properties": {
            "text": {
                "type": "text",
                "analyzer": "english"
            }
        }
    }
    try:
        if not client.indices.exists(index = index_name):
            client.indices.create(index = index_name, body = {"mappings": mappings})
            print(f"Index '{index_name}' created successfully")
        else:
            mapping_response = client.indices.put_mapping(index = index_name, body = mappings)
            print("Mapping updated:", mapping_response)
    except Exception as e:
        print(f"Index creation/mapping error: {e}")

def upload_data(docs, timeout = 120):
    try:
        bulk_response = helpers.bulk(
            client,
            docs,
            request_timeout = timeout
        )
        print("Bulk inserted:", bulk_response)
    except Exception as e:
        print(f"Bulk insert error: {e}")

def list_all_document_ids(size = 10000) -> List[str]:
    try:
        search_response = client.search(
            index = index_name,
            body = {
                "query": {"match_all": {}},
                "_source": False,  # Only get IDs, not content
                "size": size
            }
        )
        doc_ids = [hit['_id'] for hit in search_response['hits']['hits']]
        print(f"Found {len(doc_ids)} documents:")
        for doc_id in doc_ids:
            print(str(doc_id))
        return doc_ids
            
    except Exception as e:
        print(f"Error listing document IDs: {e}")
        return []
    
def remove_all_documents():
    try:
        print(f"Removing all documents from index '{index_name}'")
        count_before = client.count(index = index_name)['count']
        if count_before == 0:
            print("No documents to delete.")
            return
        client.delete_by_query(
            index = index_name,
            body = {
                "query": {
                    "match_all": {}
                }
            },
            wait_for_completion = True
        )
    except Exception as e:
        print(f"Error removing all documents: {e}")

def delete_all_indices():
    try:
        indices = client.indices.get_alias(index = "*")
        if not indices:
            print("No indices found.")
            return

        for index in indices:
            print(f"Deleting index: {index}")
            client.indices.delete(index = index)
    except Exception as e:
        print(f"Error deleting indices: {e}")

def elastic_setup():
    connection_test()
    create_mapping()

    docs = []
    folder_path = "playground/khoanth/data_setup/cleaned_documents"
    for name in collections:
        with open (f"{folder_path}/{name}/document.json", "r", encoding = "utf-8") as f:
            doc_str = ' '.join([line.strip() for line in f if line.strip()])
        process = 0
        total = len(doc_str) // CHUNK_SIZE + bool(len(doc_str) % CHUNK_SIZE)
        for i in range (0, len(doc_str), CHUNK_SIZE):
            bound = min(i + CHUNK_SIZE - 1, len(doc_str))
            docs.append({
                "_index": index_name,
                "_source": {
                   "text": doc_str[i:bound]
                }
            })
            process += 1
            print (f"Uploaded {process}/{total}")
    upload_data(docs)

if __name__ == "__main__":
    elastic_setup()