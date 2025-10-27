import json, os, pickle, re, uuid
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain.schema import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.models import VectorParams, Distance, PointStruct

class QdrantSetup:
    def __init__(self, embedding_model:str, timeout = None):
        self.__embedding_model = HuggingFaceEmbeddings(model_name = embedding_model)
        self.__collections = ["civil_law", "criminal_law", "environmental_law", "international_law", "labor_and_employment_law"]
        self.__client = QdrantClient(
            url = os.getenv("QDRANT_URL"),
            api_key = os.getenv("QDRANT_API_KEY"),
            timeout = timeout
        )

    def __load_cleaned_documents(self, sub:str, path = "data_setup"):
        cleaned_docs = []
        folder_path = f"{path}/raw_documents/{sub}"
        storage_path = f"{path}/cleaned_documents/{sub}/document.pkl"
        if os.path.exists(storage_path):
            with open(storage_path, "rb") as f:
                return pickle.load(f)
        for file_name in os.listdir(folder_path):
            if not file_name.endswith(".pdf"):
                continue
            file_path = os.path.join(folder_path, file_name)
            loader = PyPDFLoader(file_path)
            raw_docs = loader.load()
            for doc in raw_docs:
                clean = doc.page_content.encode('ascii', errors = 'ignore').decode()
                clean = re.sub(r'\s+', ' ', clean).replace('.', '').strip()
                cleaned_docs.append(Document(page_content = clean, metadata = doc.metadata))
        with open(storage_path, "wb") as f:
            pickle.dump(cleaned_docs, f)
        return cleaned_docs

    def __upload_to_qdrant(self, doc, collection_name):
        splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            chunk_size = 450,
            chunk_overlap = 50
        )
        chunks = splitter.split_documents(doc)
        batch_size = 50
        total_batches = (len(chunks) + batch_size - 1) // batch_size
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            batch_texts = [chunk.page_content for chunk in batch]
            batch_vectors = self.__embedding_model.embed_documents(batch_texts)
            points = [
                PointStruct(
                    id = str(uuid.uuid4()),
                    vector = vector,
                    payload = {
                        "text": chunk.page_content,
                        "metadata": chunk.metadata
                    }
                ) for chunk, vector in zip(batch, batch_vectors)
            ]
            self.__client.upsert(
                collection_name = collection_name,
                points = points
            )
            print(f"""
                [INFO] [backend.data_setup.qdrant_setup] Uploaded {i // batch_size + 1}/{total_batches} over {len(points)} chunks
            """)
        
    def qdrant_setup(self): 
        collection_config = models.VectorParams(
                size = 384, # vector dimension
                distance = models.Distance.COSINE
            )
        try:
            if self.__client.get_collections("chat_pool"):
                print(f"""
                    [INFO] [backend.data_setup.qdrant_setup] Collection 'chat_pool' already exists
                """)
        except Exception:
            self.__client.create_collection(
                collection_name = "chat_pool",
                vectors_config = collection_config
            )
            print(f"""
                [INFO] [backend.data_setup.qdrant_setup] Created collection 'chat_pool'
            """)

        for collection_name in self.__collections:
            try:
                if self.__client.get_collection(collection_name):
                    print(f"""
                        [INFO] [backend.data_setup.qdrant_setup] Collection '{collection_name}' already exists
                    """)
                    raise PermissionError("status_code: 403")
            except:
                self.__client.create_collection(
                    collection_name = collection_name,
                    vectors_config = collection_config
                )
                print(f"""
                    [INFO] [backend.data_setup.qdrant_setup] Created collection '{collection_name}'
                """)
        
        for qdrant_collection in self.__collections:
            doc = self.__load_cleaned_documents(sub = qdrant_collection)
            elastic_str = ' '.join(d.page_content for d in doc)
            with open(f"data_setup/cleaned_documents/{qdrant_collection}/document.json", "w", encoding = 'utf-8') as f:
                f.write(elastic_str)
            self.__upload_to_qdrant(doc, qdrant_collection)

        self.__client.close()