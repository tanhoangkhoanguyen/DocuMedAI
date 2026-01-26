import warnings
warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()
import os, random, pytz, uuid, json
from datasets import load_dataset
from itertools import islice
from langchain.text_splitter import TokenTextSplitter
from datetime import datetime
from langchain_community.embeddings import HuggingFaceEmbeddings

class DataProcessing:
    def __init__(
            self,
            dataset_name: str,
            embedding_model: str
        ):
        self.dataset_name = dataset_name
        self.raw_dataset = None
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__uuid_namespace = uuid.UUID(os.getenv("UUID_NAMESPACE"))

    def load_dataset(self):
        try:
            # dataset = load_dataset(self.dataset_name)
            # self.raw_dataset = dataset["train"].shuffle(seed = 42)
            self.raw_dataset = load_dataset(self.dataset_name, split = "train", streaming = True)
            print(f"""
                [INFO] [backend.vector_database_tests.main] Loaded dataset '{self.dataset_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_database_tests.main] Failed to load dataset '{self.dataset_name}'
            """)
    
    def embed_query(
            self,
            query: str
        ):
        embedded_query = self.__embedding_model.embed_query(query)
        return embedded_query

    def generated_queries(
            self,
            output_path: str = "vector_database_tests/generated_queries"
        ):
        try:
            num_queries = 550
            output_path = os.path.join(output_path, self.dataset_name.replace("/", "-") + ".jsonl")
            with open(output_path, 'w', encoding = "utf-8") as f:
                for data in self.raw_dataset:
                    query = f"{data['title']} definition"
                    embedded_query = self.embed_query(query)
                    record = {
                        "query": query,
                        "embedded_query": embedded_query,
                        "answer": data["text"]
                    }
                    f.write(json.dumps(record, ensure_ascii = False) + '\n')
                    num_queries -= 1
                    if num_queries == 0:
                        break
            print(f"""
                [INFO] [backend.vector_database_tests.main] Generated queries for dataset '{self.dataset_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_database_tests.main] Failed to create queries for dataset '{self.dataset_name}'
                \t{str(e)}
            """)

    def __split_dataset(self):
        try:


            cheating = 25185


            for data in self.raw_dataset:


                if cheating > 0:
                    cheating -= 1
                    if cheating % 100 == 0:
                        print ("A few more ", cheating)
                    continue


                chunks = TokenTextSplitter(
                    chunk_size = 512,
                    chunk_overlap = 64
                ).split_text(data["text"])

                for chunk in chunks:
                    id = str(datetime.now(pytz.utc))
                    hashed_id = str(uuid.uuid5(self.__uuid_namespace, id))
                    yield {
                        "id": hashed_id,
                        "page_title": data["title"],
                        "split_text": chunk,
                    }
            print(f"""
                [INFO] [backend.vector_database_tests.main] Split dataset '{self.dataset_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_database_tests.main] Failed to split dataset '{self.dataset_name}'
                \t{str(e)}
            """)

    def __embed_dataset(
            self,
            chunks
        ):
        try:
            for chunk in chunks:
                chunk["embedded_test"] = self.embed_query(chunk["split_text"])
                yield chunk                    
            print(f"""
                [INFO] [backend.vector_database_tests.main] Embedded dataset '{self.dataset_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_database_tests.main] Failed to embedded dataset '{self.dataset_name}'
                \t{str(e)}
            """)

    def __save_to_jsonl(
            self,
            embedded_chunks,
            batch_size = 50000,
            output_path: str = "vector_database_tests/dataset"
        ):
        try:
            base_path = os.path.join(output_path, self.dataset_name.replace("/", "-"))


            file_count = 6
            count = 185
            f = open(f"{base_path}-{file_count}.jsonl", 'a', encoding = "utf-8")


            for chunk in embedded_chunks:
                f.write(json.dumps(chunk, ensure_ascii = False) + '\n')
                count += 1
                if count == batch_size:
                    f.close()
                    file_count += 1
                    count = 0
                    f = open(f"{base_path}-{file_count}.jsonl", 'w', encoding = "utf-8")
            print(f"""
                [INFO] [backend.vector_database_tests.main] Saved dataset '{self.dataset_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_database_tests.main] Failed to save dataset '{self.dataset_name}'
                \t{str(e)}
            """)
    
    def data_processing(self):
        self.load_dataset()
        # self.generated_queries()

        chunks = self.__split_dataset()
        embedded_chunks = self.__embed_dataset(chunks)
        self.__save_to_jsonl(embedded_chunks)

if __name__ == "__main__":
    # dataset_name = "gamino/wiki_medical_terms"
    dataset_name = "Qdrant/dbpedia-entities-openai3-text-embedding-3-large-3072-1M"
    embedding_model = "sentence-transformers/all-MiniLM-L6-v2"

    data_processing = DataProcessing(
            dataset_name = dataset_name,
            embedding_model = embedding_model
        )
    data_processing.data_processing()