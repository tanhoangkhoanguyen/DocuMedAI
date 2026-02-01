import sys, json
from elasticsearch import Elasticsearch

ELASTICSEARCH_URL = "http://la-elasticsearch:9200"

class ElasticSearchSetup:
    def __init__(
            self,
            index: str = "host",
            chunk_size: int = 2048,        # 512 tokens * 4
        ):
        self.__index = index
        self.__chunk_size = chunk_size
        self.__client = Elasticsearch(
            ELASTICSEARCH_URL, 
            verify_certs = False
        )

    def __test_connection(self):
        try:
            info = self.__client.info()
            print(f"""
                [INFO] [backend.data_setup.elasticsearch_setup] Connected to ElasticSearch:
                \tVersion: {info['version']['number']}
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.elasticsearch_setup] Failed to connect to ElasticSearch:
                \t{str(e)}
            """)
            raise

    def __create_index(
            self, 
            index: str
        ):
        mappings = {
            "properties": {
                "text": {
                    "type": "text",
                    "analyzer": "english"
                }
            }
        }
        try:
            if self.__client.indices.exists(index = index):
                self.__client.indices.delete(index = index)
            self.__client.indices.create(index = index, body = {"mappings": mappings})
            print(f"""
                [INFO] [backend.data_setup.elasticsearch_setup] Created index '{index}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.elasticsearch_setup] Failed to create index '{index}':
                \t{str(e)}
            """)
            raise

    def __push_to_index(
            self, 
            index: str, 
            text: str
        ):
        try:
            self.__client.index(
                index = index,
                document = {
                    "text": text
                }
            )
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.elasticsearch_setup] Failed to push mapping:
                \t{str(e)}
            """)

    def execute(
            self,
            input_path: str = "data_setup/cleaned_documents/example_document.jsonl"
        ):
        self.__test_connection()
        self.__create_index(self.__index)

        with open(input_path, "r", encoding = "utf-8") as f:
            for line in f:
                record = json.loads(line)
                
                for i in range (0, len(record), self.__chunk_size):
                    boundary = min(i + self.__chunk_size, len(record))
                    self.__push_to_index(self.__index, record["content"][i:boundary])
        
if __name__ == "__main__":
    user_input = input("Type 'Execute' to run: ")
    if user_input != "Execute":
        raise

    elasticsearch_setup = ElasticSearchSetup()
    elasticsearch_setup.execute()