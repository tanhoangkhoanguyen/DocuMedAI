import os, sys, warnings, pickle, json
from elasticsearch import Elasticsearch, helpers
from datetime import datetime
from typing import List, Dict, Any, Optional

class ElasticSetup:
    def __init__(self, chunk_size:int):
        self.__chunk_size = chunk_size
        self.__collections = ["civil_law", "criminal_law", "environmental_law", "international_law", "labor_and_employment_law"]
        self.__client = Elasticsearch(
            [os.getenv("ELASTIC_HOST")],
            basic_auth = ("elastic", os.getenv("ELASTIC_PASSWORD")),
            verify_certs = False,
            ssl_show_warn = False
        )

    def __connection_test(self):
        try:
            info = self.__client.info()
            print("Connected to Elasticsearch:")
            print(f"Cluster: {info['cluster_name']}")
            print(f"Version: {info['version']['number']}")
        except Exception as e:
            print(f"Connection failed: {e}")
            sys.exit()

    def __create_mapping(self, index_name):
        mappings = {
            "properties": {
                "text": {
                    "type": "text",
                    "analyzer": "english"
                }
            }
        }
        try:
            if not self.__client.indices.exists(index = index_name):
                self.__client.indices.create(index = index_name, body = {"mappings": mappings})
                print(f"Index '{index_name}' created successfully")
            else:
                mapping_response = self.__client.indices.put_mapping(index = index_name, body = mappings)
                print("Mapping updated:", mapping_response)
        except Exception as e:
            print(f"Index creation/mapping error: {e}")

    def __upload_data(self, doc, timeout = None):
        try:
            bulk_response = helpers.bulk(
                self.__client,
                doc,
                request_timeout = timeout
            )
            print("Bulk inserted:", bulk_response)
        except Exception as e:
            print(f"Bulk insert error: {e}")

    def __list_all_document_ids(self, index_name, size = 10000) -> List[str]:
        try:
            search_response = self.__client.search(
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
        
    def __remove_all_documents(self, index_name):
        try:
            print(f"Removing all documents from index '{index_name}'")
            count_before = self.__client.count(index = index_name)['count']
            if count_before == 0:
                print("No documents to delete.")
                return
            self.__client.delete_by_query(
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

    def __delete_all_indices(self):
        try:
            indices = self.__client.indices.get_alias(index = "*")
            if not self.__client:
                print("No indices found.")
                return

            for index in indices:
                if index == ".security-7":
                    continue
                print(f"Deleting index: {index}")
                self.__client.indices.delete(index = index)
        except Exception as e:
            print(f"Error deleting indices: {e}")

    def elastic_setup(self):
        self.__connection_test()
        for name in self.__collections:
            self.__create_mapping(name)

        doc_dict = {
            "civil_law": [],
            "criminal_law": [],
            "environmental_law": [],
            "international_law": [],
            "labor_and_employment_law": []
        }
        folder_path = "data_setup/cleaned_documents"
        for name in self.__collections:
            with open (f"{folder_path}/{name}/document.json", "r", encoding = "utf-8") as f:
                doc_str = ' '.join([line.strip() for line in f if line.strip()])
            for i in range (0, len(doc_str), self.__chunk_size):
                bound = min(i + self.__chunk_size - 1, len(doc_str))
                doc_dict[name].append({
                    "_index": name,
                    "_source": {
                    "text": doc_str[i:bound]
                    }
                })
        for _, doc in doc_dict.items():
            self.__upload_data(doc)