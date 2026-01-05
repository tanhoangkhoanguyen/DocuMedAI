import os, sys, json, uuid, pytz, warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()
from qdrant_client import QdrantClient
from qdrant_client.http.models import VectorParams, Distance, PointStruct
from langchain_community.embeddings import HuggingFaceEmbeddings
from datetime import datetime

QDRANT_URL = "http://la-qdrant:6333"

class QdrantSetup:
    def __init__(
            self, 
            embedding_model: str,
            collection_name: str = "host",
            chunk_size: int = 2048,        # 512 tokens * 4
        ):
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__collection_name = collection_name
        self.__chunk_size = chunk_size
        self.__client = QdrantClient(url = QDRANT_URL)
        self.__uuid_namespace = uuid.UUID(os.getenv("UUID_NAMESPACE"))

    def __create_collection(
            self, 
            collection_name: str
        ):
        try:
            if self.__client.collection_exists(collection_name):
                self.__client.delete_collection(collection_name)
            self.__client.create_collection(
                collection_name = self.__collection_name,
                vectors_config = VectorParams(
                    size = 384, 
                    distance = Distance.COSINE
                )
            )
            print(f"""
                [INFO] [backend.data_setup.qdrant_setup] Created collection '{collection_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.qdrant_setup] Failed to create collection '{collection_name}'
                \t{str(e)}
            """)
            sys.exit()

    def __push_to_collection(
            self,
            collection_name: str,
            text: str
        ):
        try:
            point_id = str(datetime.now(pytz.utc))
            hashed_point_id = uuid.uuid5(self.__uuid_namespace, point_id)
            embedded_text = self.__embedding_model.embed_query(text)

            points = [
                PointStruct(
                    id = hashed_point_id,
                    vector = embedded_text,
                    payload = {"text": text}
                )
            ]
            self.__client.upsert(
                collection_name = collection_name, 
                points = points
            )
        except Exception as e:
            print(f"""
                [ERROR] [backend.data_setup.qdrant_setup] Failed to push to collection '{collection_name}'
                \t{str(e)}
            """)

    def execute(
            self,
            input_path: str = "data_setup/cleaned_documents/example_document.jsonl"
        ):
        self.__create_collection(self.__collection_name)
        
        with open (input_path, "r", encoding = "utf-8") as f:
            for line in f:
                record = json.loads(line)

                for i in range (0, len(record), self.__chunk_size):
                    boundary = min(i + self.__chunk_size, len(record))
                    self.__push_to_collection(self.__collection_name, record["content"][i:boundary])

        self.__client.close()

if __name__ == "__main__":
    user_input = input("Type 'Execute' to run: ")
    if user_input != "Execute":
        sys.exit()

    embedding_model = "all-MiniLM-L6-v2"

    qdrant_setup = QdrantSetup(
        embedding_model = embedding_model
    )
    qdrant_setup.execute()