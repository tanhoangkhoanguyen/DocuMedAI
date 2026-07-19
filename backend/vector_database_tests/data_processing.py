from dotenv import load_dotenv
load_dotenv()
import os, json, warnings
warnings.filterwarnings("ignore")
from datasets import load_dataset
from langchain_text_splitters import TokenTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from utils.pattern_cipher import get_pattern_cipher

class DataProcessing:
    def __init__(
            self,
            dataset_name: str,
            embedding_model: str
        ):
        self.dataset_name = dataset_name
        self.raw_dataset = None
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__pattern_cipher = get_pattern_cipher()

    def load_dataset(self):
        try:
            # dataset = load_dataset(self.dataset_name)
            # self.raw_dataset = dataset["train"].shuffle(seed = 42)
            self.raw_dataset = load_dataset(self.dataset_name, split = "train", streaming = True)
            print(f"""
                [INFO] [backend.vector_database_tests.data_processing] Loaded dataset '{self.dataset_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_database_tests.data_processing] Failed to load dataset '{self.dataset_name}'
            """)
    
    def embed_query(
            self,
            query: str
        ):
        embedded_query = self.__embedding_model.embed_query(query)
        return embedded_query

    def generated_queries(
            self,
            num_queries: int,
            output_path: str = "vector_database_tests/generated_queries"
        ):
        try:
            output_path = f"{output_path}/{self.dataset_name.replace('/', '-')}.jsonl"
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
                [INFO] [backend.vector_database_tests.data_processing] Generated queries for dataset '{self.dataset_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_database_tests.data_processing] Failed to create queries for dataset '{self.dataset_name}'
                \t{str(e)}
            """)

    def __split_dataset(self):
        try:
            seen = set()
            for data in self.raw_dataset:
                chunks = TokenTextSplitter(
                    chunk_size = 512,
                    chunk_overlap = 64
                ).split_text(data["page_text"])

                for chunk in chunks:
                    if chunk in seen:
                        continue
                    else:
                        seen.add(chunk)

                    # hash_user_id folds in a timestamp => a fresh unique id per chunk.
                    hashed_id = self.__pattern_cipher.hash_user_id(chunk)
                    yield {
                        "id": hashed_id,
                        "title": data["page_title"],
                        "split_text": chunk,
                    }
            print(f"""
                [INFO] [backend.vector_database_tests.data_processing] Split dataset '{self.dataset_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_database_tests.data_processing] Failed to split dataset '{self.dataset_name}'
                \t{str(e)}
            """)

    def __embed_dataset(
            self,
            chunks
        ):
        try:
            for chunk in chunks:
                chunk["embedded_test"] = self.embed_query(chunk["split_text"]) # embedded_text
                yield chunk                    
            print(f"""
                [INFO] [backend.vector_database_tests.data_processing] Embedded dataset '{self.dataset_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_database_tests.data_processing] Failed to embedded dataset '{self.dataset_name}'
                \t{str(e)}
            """)

    def __save_to_jsonl(
            self,
            embedded_chunks,
            batch_size: int = 50000,
            output_path: str = "vector_database_tests/dataset"
        ):
        try:
            base_path = f"{output_path}/{self.dataset_name.replace('/', '-')}"

            file_count = 1
            count = 0
            f = open(f"{base_path}-{file_count}.jsonl", 'w', encoding = "utf-8")

            for chunk in embedded_chunks:
                f.write(json.dumps(chunk, ensure_ascii = False) + '\n')
                count += 1
                if count == batch_size:
                    f.close()
                    file_count += 1
                    count = 0
                    f = open(f"{base_path}-{file_count}.jsonl", 'w', encoding = "utf-8")
            print(f"""
                [INFO] [backend.vector_database_tests.data_processing] Saved dataset '{self.dataset_name}'
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_database_tests.data_processing] Failed to save dataset '{self.dataset_name}'
                \t{str(e)}
            """)
    
    def describe_folder(
            self,
            folder_path: str,
            field: str
        ):
        try:
            count = 0
            duplicate = 0
            seen = set()

            for filename in os.listdir(folder_path):
                if not filename.endswith(".jsonl"):
                    continue
                
                file_path = f"{folder_path}/{filename}"
                with open(file_path, "r", encoding = "utf-8") as f:
                    for line in f:
                        record = json.loads(line)
                        text = record[field]
                        if text in seen:
                            duplicate += 1
                        else:
                            seen.add(text)
                        count += 1
            print(f"""
                [INFO] [backend.vector_database_tests.data_processing]
                \tCount:     {count}
                \tDuplicate: {duplicate}
            """)
        except Exception as e:
            print(f"""
                [ERROR] [backend.vector_database_tests.data_processing] Failed to describe dataset folder
                \t{str(e)}
            """)

    def data_processing(self, num_queries):
        self.load_dataset()
        self.generated_queries(num_queries = num_queries)

        chunks = self.__split_dataset()
        embedded_chunks = self.__embed_dataset(chunks)
        self.__save_to_jsonl(embedded_chunks)

        self.describe_folder(
            folder_path = "vector_database_tests/generated_queries",
            field = "query"
        )
        self.describe_folder(
            folder_path = "vector_database_tests/dataset",
            field = "split_text"
        )

if __name__ == "__main__":
    # dataset_name = "gamino/wiki_medical_terms"
    dataset_name = "Qdrant/dbpedia-entities-openai3-text-embedding-3-large-3072-1M"
    embedding_model = "sentence-transformers/all-MiniLM-L6-v2"

    data_processing = DataProcessing(
            dataset_name = dataset_name,
            embedding_model = embedding_model
        )
    data_processing.data_processing(num_queries = 100000)