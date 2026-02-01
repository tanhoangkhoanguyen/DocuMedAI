import sys, weaviate, warnings
warnings.filterwarnings("ignore")
from langchain_community.embeddings import HuggingFaceEmbeddings
from typing import List

WEAVIATE_URL = "http://la-weaviate:8080"

class WeaviateClient:
    def __init__(
            self, 
            embedding_model: str,
            embedding_dimension: int
        ):
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__embedding_dimension = embedding_dimension
        self.__weaviate_client = weaviate.Client(url = WEAVIATE_URL)

    def list_collections(self) -> List[str]:
        try:
            schema = self.__weaviate_client.schema.get()
            classes = [cls['class'] for cls in schema.get("classes", [])]
            return classes
        except Exception as e:
            print(f"""
                  [ERROR] [backend.vector_databases_tests.utils.weaviate_client] Failed to list collections
                  \t{str(e)}
                """)
            return []
    
    def delete_collection(
            self,
            class_name: str
        ):
        try:
            self.__weaviate_client.schema.delete_class(class_name)
            print(f"""
                [INFO] [backend.vector_databases_tests.utils.weaviate_client] Deleted class '{class_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.weaviate_client] Failed to deleted class '{class_name}'
                \t{str(e)}
            """)
            raise

    def create_collection(
            self,
            class_name: str
        ):
        try:
            if class_name in self.list_collections():
                self.delete_collection(class_name)

            self.__weaviate_client.schema.create_class({
                "class": class_name,
                "vectorizer": "none",
                "properties": [
                    {
                        "name": "query",
                        "dataType": ["string"]
                    }
                ]
            })
            print(f"""
                [INFO] [backend.vector_databases_tests.utils.weaviate_client] Created class '{class_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.weaviate_client] Failed to create class '{class_name}'
                \t{str(e)}
            """)
            raise

    def embed_query(
            self,
            query: str
        ):
        embedded_query = self.__embedding_model.embed_query(query)
        return embedded_query

    def push_to_collection(
            self,
            class_name: str,
            ids: List[str],
            queries,
            embedded_queries: list
        ):
        try:
            with self.__weaviate_client.batch as batch:
                batch.batch_size = 100
                for idx in range(len(ids)):
                    batch.add_data_object(
                        data_object={"query": queries[idx]},
                        class_name=class_name,
                        vector=embedded_queries[idx],
                        uuid=ids[idx]
                    )
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.weaviate_client] Failed to push to class '{class_name}'
                \t{str(e)}
            """)

    def retrieve_query(
            self, 
            class_name: str,
            embedded_query, 
            top_k: int = 50
        ):
        try:
            response = self.__weaviate_client.query.get(class_name, ["*"]) \
                .with_near_vector({"vector": embedded_query}) \
                .with_limit(top_k) \
                .do()
            return response
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_databases_tests.utils.weaviate_client] Failed to retrieve from class '{class_name}'
                \t{str(e)}
            """)
            return []