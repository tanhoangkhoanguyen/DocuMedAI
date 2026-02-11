import requests, json, warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from typing import List

VESPA_URL = "http://la-vespa:8080"

class VespaClient:
    def __init__(
            self, 
            embedding_model: str,
            embedding_dimension: int
        ):
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__embedding_dimension = embedding_dimension

    def list_collections(self) -> List[str]:
        try:
            query_body = {
                "yql": 'select collection from vector where true',
                "hits": 400
            }
            response = requests.post(f"{VESPA_URL}/search/", json=query_body).json()
            hits = response.get("root", {}).get("children", [])
            collections = set()
            for doc in hits:
                collection_name = doc.get("fields", {}).get("collection")
                if collection_name:
                    collections.add(collection_name)
            return list(collections)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.vespa_client] Failed to list collections
                \t{str(e)}
                """)
            return []

    def embed_query(
            self,
            query: str
        ):
        embedded_query = self.__embedding_model.embed_query(query)
        return embedded_query

    def delete_collection(
            self,
            collection_name: str
        ):
        try:
            query_body = {
                "yql": f'select id from vector where collection = "{collection_name}"',
                "hits": 1000
            }
            response = requests.post(f"{VESPA_URL}/search/", json = query_body).json()
            hits = response.get("root", {}).get("children", [])
            print(f"""
                [INFO] [backend.vector_databases_tests.utils.vespa_client] Collection '{collection_name}' doesnt exist
            """)

            if hits:
                for hit in hits:
                    doc_id = hit["id"]
                    delete_url = doc_id.replace("id:", f"{VESPA_URL}/document/v1/")
                    requests.delete(delete_url)
                print(f"""
                    [INFO] [backend.vector_databases_tests.utils.vespa_client] Delete collection '{collection_name}'
                """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.vespa_client] Failed to delete collection '{collection_name}'
                \t{str(e)}
            """)
            raise

    def create_collection(self, collection_name: str):
        try:
            self.delete_collection(collection_name)
            doc = {
                "fields": {
                    "collection": collection_name,
                    "query": "__dummy__",
                    "embedded_query": [0.0] * self.__embedding_dimension
                }
            }
            url = f"{VESPA_URL}/document/v1/mynamespace/vector/docid/{collection_name}-dummy"
            requests.post(url, json = doc)
            print(f"""
                [INFO] [backend.vector_databases_tests.utils.vespa_client] Created collection '{collection_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.vespa_client] Failed to create collection '{collection_name}'
                \t{str(e)}
            """)
            raise

    def push_to_collection(
            self,
            collection_name: str,
            ids: List[str],
            queries: List[str],
            embedded_queries,
        ):
        try:
            for idx in range(len(ids)):
                id = ids[idx]
                query = queries[idx]
                emebedded_query = embedded_queries[idx]
                doc = {
                    "fields": {
                        "collection": collection_name,
                        "query": query,
                        "embedded_query": emebedded_query
                    }
                }
                url = f"{VESPA_URL}/document/v1/mynamespace/vector/docid/{id}"
                response = requests.post(
                    url,
                    json = doc
                )
                response.raise_for_status()
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.vespa_client] Failed to push to collection '{collection_name}'
                \t{str(e)}
            """)

    def retrieve_query(
            self,
            collection_name: str,
            embedded_query,
            top_k: int = 50
        ):
        try:
            query_body = {
                "yql": f"""
                    select * from vector
                    where collection = "{collection_name}"
                    and {{targetHits:5000}}nearestNeighbor(embedded_query, query_embedding)
                """,
                "input.query(query_embedding)": embedded_query,
                "ranking.profile": "default",
                "hits": top_k
            }
            response = requests.post(
                    f"{VESPA_URL}/search/",
                    json = query_body
                ).json()
            return response
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.vespa_client] Failed to retrieve from collection '{collection_name}'
                \t{str(e)}
            """)
            return []