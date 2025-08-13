# ⚖️ US LAW ADVISORY
[![Python](https://img.shields.io/badge/Python-3.10-yellow)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111.0-009688)](https://fastapi.tiangolo.com/)
[![Qdrant](https://img.shields.io/badge/Qdrant-1.7.3-orange)](https://qdrant.tech/)
[![Elasticsearch](https://img.shields.io/badge/Elasticsearch-8.13.4-005571)](https://www.elastic.co/elasticsearch/)

![system_overview](backend/services/chatbot/assets/system_overview.png)

## 📌 Why This Project?
Accessing clear, reliable, and current U.S. legal information can be overwhelming - laws cover many areas, resources are scattered, and finding the right answer often takes hours. We built US LAW ADVISORY to help students and the public quickly find trustworthy legal references, save time, and navigate complex legal topics without the frustration of endless searching.

## 📜 Overview
**US LAW ADVISORY** is an AI-powered **Retrieval-Augmented Generation (RAG)** chatbot that helps users search and understand U.S. legal topics across:

- **Criminal Law**
- **Environmental Law**
- **Civil Law**
- **International Law**
- **Labor & Employment Law**

## ✨ Key Features
| Feature | Description |
|---------|-------------|
| **Intelligent Legal Search** | Combines Qdrant (vector search) & Elasticsearch (keyword search) |
| **RAG Pipeline** | Generates grounded, context-aware responses |
| **Multi-Domain Support** | Handles multiple legal categories |
| **Advanced Models** | `all-MiniLM_L6-v2` for embeddings + `bge-reranker-v2-m3` for reranking |

## 🛠 Technology Stack
**Backend**  
- Python 3.10  
- FastAPI (REST API)  
- Docker Compose (container orchestration)

**AI & Retrieval**  
- Qdrant – Vector database  
- Elasticsearch – Keyword-based indexing  
- Sentence Transformers – Embeddings  
- BAAI Reranker – Precision boosting

## 🚀 Getting Started
### 1. Prerequisites
- Docker & Docker Compose installed  
- Ports `9002`, `9004`, and `9005` available  

### 2. Setup & Run
```bash
git clone https://github.com/tanhoangkhoanguyen/lawAdvisory.git
cd lawAdvisory
docker compose up -d --build
```

### 3. Upload Data to Qdrant
```bash
python -m services.data_setup.data_upload
```
> 💡 Ensure `.env` is configured before running this step.

### 4. Access Services
| Service                  | Purpose                    | URL                                            |
| ------------------------ | -------------------------- | ---------------------------------------------- |
| **Elasticsearch Status** | Check ES health            | [http://localhost:9002](http://localhost:9002) |
| **Swagger UI**           | Test backend API endpoints | [http://localhost:9004](http://localhost:9004) |
| **Chatbot UI**           | Interact with the chatbot  | [http://localhost:9005](http://localhost:9005) |

## 📂 Repository Structure
```
backend/
 ├─ data_setup/
 │   ├─ cleaned_documents/
 │   │   ├─ civil_law/
 │   │   │   ├─ document.json                         # Cleaned civil law data (json)
 │   │   │   └─ document.pkl                          # Cleaned civil law data (pkl)
 │   │   ├─ criminal_law/
 │   │   │   ├─ document.json                         # Cleaned criminal law data (json)
 │   │   │   └─ document.pkl                          # Cleaned criminal law data (pkl)
 │   │   ├─ environmental_law/
 │   │   │   ├─ document.json                         # Cleaned environmental law data (json)
 │   │   │   └─ document.pkl                          # Cleaned environmental law data (pkl)
 │   │   ├─ international_law/
 │   │   │   ├─ document.json                         # Cleaned international law data (json)
 │   │   │   └─ document.pkl                          # Cleaned international law data (pkl)
 │   │   └─ labor_and_employment_law/
 │   │       ├─ document.json                         # Cleaned labor & employment law data (json)
 │   │       └─ document.pkl                          # Cleaned labor & employment law data (pkl)
 │   │
 │   ├─ raw_documents/
 │   │   ├─ civil_law/
 │   │   │   └─ federal-rules-of-civil-procedure.pdf  # Raw source document
 │   │   ├─ criminal_law/
 │   │   │   ├─ Barkow.Crim_.Full_.Sp14.pdf
 │   │   │   ├─ Criminal law by Wilson, William (z-lib.org).pdf
 │   │   │   ├─ Criminal Law.pdf
 │   │   │   ├─ Criminal-Law-1614009771._print.pdf
 │   │   │   ├─ criminal-law-cases-statutes-and-lawyering-strategies-4nbsped-1531018858-9781531018856.pdf
 │   │   │   ├─ Full.pdf
 │   │   │   └─ Textbook_Criminal-Law.pdf
 │   │   ├─ environmental_law/
 │   │   │   ├─ 5173476.pdf
 │   │   │   ├─ international-environmental-law.pdf
 │   │   │   └─ RL30798.pdf
 │   │   ├─ international_law/
 │   │   │   ├─ book_1.pdf
 │   │   │   └─ IInd Term_Public InternationalLaw_LB205_2022 .pdf
 │   │   └─ labor_and_employment_law/
 │   │       ├─ Gold_An_Introduction_to_Labor_Law003.pdf
 │   │       ├─ laboremployment2012-1.pdf
 │   │       ├─ Labour_Law_Interactive_PDF_03_07_2021.pdf
 │   │       └─ us-labor-and-employment-laws-english.pdf
 │   │
 │   ├─ data_upload.py                                # Upload cleaned data to vector DBs
 │   ├─ elastic_setup.py                              # Elasticsearch index setup
 │   ├─ mongodb_setup.py                              # MongoDB initialization script
 │   └─ qdrant_setup.py                               # Qdrant collection setup
 │
 ├─ services/chatbot/                                
 │   ├─ app/
 │   │   ├─ main.py                                  # API entry point
 │   │   ├─ route.py                                 # Defines API routes
 │   │   └─ request_schemas.py                       # Pydantic request/response models
 │   │
 │   ├─ assets/
 │   │   ├─ graph.png                                # Architecture diagram
 │   │   └─ system_overview.png                      # System overview diagram
 │   │
 │   └─ core/
 │       ├─ agents/
 │       │   ├─ general.py                           # General-purpose chatbot agent
 │       │   └─ law_advisory.py                      # Law advisory agent
 │       │
 │       ├─ constants/
 │       │   ├─ prompts.py                           # Prompt templates
 │       │   └─ schemas.py                           # Schema definitions
 │       │
 │       ├─ retriever/
 │       │   ├─ elastic.py                           # Elasticsearch retriever
 │       │   ├─ qdrant.py                            # Qdrant retriever
 │       │   └─ tavily.py                            # Tavily API retriever
 │       │
 │       ├─ tools/
 │       │   ├─ helper.py                            # Query transformation
 │       │   ├─ rerank.py                            # Reranking retrieved
 │       │   └─ retrieve_context.py                  # Building final context
 │       │
 │       ├─ main.py                                  # Core chatbot orchestration
 │       └─ workflow.py                              # Defines chatbot workflow logic
 |
 ├─ test/chatbot.py                                  # System tests
 
frontend/
 ├─ app.py                                           # Main frontend app entry point
 ├─ run.py                                           # Development server runner
 ├─ constants/chatbot_schemas.py                     # Shared chatbot schemas
 └─ requirements.txt                                 # Frontend dependencies

qdrant_config/                                       # Qdrant config files
qdrant_data/                                         # Local Qdrant DB storage
docker-compose.yml                                   # Service orchestration
.env                                                 # Environment variables
README.md                                            # Project documentation
```

## 🗝 Environment Variables
Create a `.env` in the project root:
```env
# === API Keys ===
OPENAI_API_KEY=your_openai_api_key
TAVILY_API_KEY=your_tavily_api_key
TAVILY_SEARCH_URL=https://api.tavily.com/search

# === LangChain Tracing === (optional)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_API_KEY=your_langchain_api_key
LANGCHAIN_PROJECT=lawAdvisory

# === Qdrant Config ===
QDRANT_API_KEY=your_qdrant_api_key
QDRANT_URL=https://your-qdrant-instance-url

# === Elasticsearch Config ===
ELASTIC_PASSWORD=your_elastic_password
ELASTIC_HOST=http://la-elasticsearch:9200
ELASTIC_API_KEY=your_elastic_api_key
# Default username: elastic

# === MongoDB Config ===
MONGO_INITDB_ROOT_USERNAME=your_mongo_username
MONGO_INITDB_ROOT_PASSWORD=your_mongo_password
MONGODB_URI=mongodb+srv://<username>:<password>@<cluster-url>/?retryWrites=true&w=majority

# === Service Ports ===
CHATBOT_SERVICE_PORT=9004
```

## 🤝 Contributing
1. Fork this repository
2. Create a feature branch
3. Commit changes
4. Open a Pull Request

## 📜 License
MIT License – see [LICENSE](https://mit-license.org/)


<p align="center">
  <i>Built with 💙 for a more advanced future</i>
</p>