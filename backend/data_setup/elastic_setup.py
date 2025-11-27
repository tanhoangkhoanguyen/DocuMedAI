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
            print(f"""
                [INFO] [backend.data_setup.elastic_setup] Connected to ElasticSearch:
                \tCluster: {info['cluster_name']}
                \tVersion: {info['version']['number']}
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.elastic_setup] Failed to connect to ElasticSearch:
                \t{str(e)}
            """)
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
                print(f"""
                    [INFO] [backend.data_setup.elastic_setup] Created index '{index_name}'
                """)
            else:
                mapping_response = self.__client.indices.put_mapping(index = index_name, body = mappings)
                print(f"""
                    [INFO] [backend.data_setup.elastic_setup] Updated mapping index '{index_name}'
                    \t{mapping_response}
                """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.elastic_setup] Failed to update index '{index_name}':
                \t{str(e)}
            """)

    def __upload_data(self, doc, timeout = None):
        try:
            bulk_response = helpers.bulk(
                self.__client,
                doc,
                request_timeout = timeout
            )
            print(f"""
                [INFO] [backend.data_setup.elastic_setup] Inserted bulk:
                \t{bulk_response}
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.elastic_setup] Failed to insert bulk:
                \t{str(e)}
            """)

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
            print(f"""
                [INFO] [backend.data_setup.elastic_setup] Found {len(doc_ids)} documents
                \t{doc_ids}
            """)
            return doc_ids 
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.elastic_setup] Failed to list documents:
                \t{str(e)}
            """)
            return []
        
    def __remove_all_documents(self, index_name):
        try:
            count_before = self.__client.count(index = index_name)['count']
            if count_before > 0:
                self.__client.delete_by_query(
                    index = index_name,
                    body = {
                        "query": {
                            "match_all": {}
                        }
                    },
                    wait_for_completion = True
                )
            print(f"""
                [INFO] [backend.data_setup.elastic_setup] Removed all documents from index '{index_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.elastic_setup] Failed to removed all documents from index '{index_name}'
                \t{str(e)}
            """)

    def __delete_all_indices(self):
        try:
            indices = self.__client.indices.get_alias(index = "*")
            if self.__client:
                for index in indices:
                    if index == ".security-7":
                        continue
                    self.__client.indices.delete(index = index)
            print(f"""
                [INFO] [backend.data_setup.elastic_setup] Removed all indices
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.elastic_setup] Failed to removed all indices
                \t{str(e)}
            """)

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