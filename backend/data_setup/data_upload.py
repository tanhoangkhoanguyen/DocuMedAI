from data_setup.mongodb_setup import MongoDBSetup

import sys, os, re, json
from langchain_community.document_loaders import PyPDFLoader

def clean_text(text: str) -> str:
    text = text.encode("ascii", errors = "ignore").decode()    # Remove non-ASCII characters
    text = re.sub(r"\s+", " ", text)                           # Normalize whitespace
    text = text.strip()                                        # Remove whitespace from both ends
    return text

def load_and_clean_pdfs(
        input_path: str = "data_setup/raw_documents",
        output_path: str = "data_setup/cleaned_documents/example_document.jsonl"    # A format where each line is a valid JSON object
    ):
    with open(output_path, 'a', encoding = "utf-8") as f:
        for file_name in os.listdir(input_path):
            if not file_name.endswith(".pdf"):
                continue

            file_path = os.path.join(input_path, file_name)
            loader = PyPDFLoader(file_path)
            pages = loader.load()

            record = {
                "file_name": file_name,
                "content": ""
            }

            for doc in pages:
                cleaned = clean_text(doc.page_content)
                if len(cleaned) < 50:                          # Skip junk pages
                    continue
                record["content"] += cleaned

            f.write(json.dumps(record, ensure_ascii = True) + '\n')

if __name__ == "__main__":
    load_and_clean_pdfs()

    mongo_configuration = MongoDBSetup()
    mongo_configuration.execute()