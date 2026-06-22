# Vector Database Latency Benchmark
This project benchmarks vector database **indexing** and **retrieval** latency across 6 popular vector databases.
* ChromaDB
* Qdrant
* Milvus
* Pinecone (no persistent)
* Vespa
* Weaviate

The evaluation measures:
* **Indexing time** for inserting a **1M** vector dataset
* **Retrieval latency** measured under a concurrent workload of **20 users over 10 minutes**

All databases are tested **under identical conditions**.

## Experiment Setup
### Hardware & Environment
Experiments were conducted on the same machine using **Docker containers**, with each database running in its own container.

All services shared the same network environment and used the `sentence-transformers/all-MiniLM-L6-v2` embedding model with **384-dimensional vectors**.

### Dataset
| Property         | Value                 |
| ---------------- | --------------------- |
| Total vectors    | 1,000,000             |
| Vector dimension | 384                   |
| Query set size   | 500 queries           |
| Distance metric  | Cosine similarity     |

Each dataset record contains:
```
{
  "id": string,
  "split_text": string,
  "embedded_test": [384 float vector]
}
```

The query workload uses **pre-generated query embeddings**.

## Benchmark Methodology
### 1. Indexing Benchmark
Vectors are uploaded using batched inserts (1000 vectors per batch). Indexing time measures **only insertion + indexing overhead**, excluding dataset loading.

### 2. Retrieval Benchmark
Retrieval tests use **Locust** to simulate query workload. Query process:
1. Randomly select queries from the retrieval dataset
2. Send ANN vector search
3. Measure end-to-end latency

Metrics collected:
* Median latency
* 95th percentile latency
* 99th percentile latency
* Average latency
* Throughput (requests/sec)

Each system was tested for **5 runs**.

## Databases Evaluated
All databases were configured to use **HNSW-based indexing** where applicable.
```
M = 64
efConstruction = 200
query ef = 64
```

### Indexing Performance
Time required to ingest **1 million vectors**.
| Database         | Indexing Time          |
| ---------------- | ---------------------- |
| ChromaDB         | 45557s                 |
| Qdrant           | 253s                   |
| Milvus           | 1258s                  |
| Pinecone (local) | 268s                   |
| Vespa            | 933s                   |
| Weaviate         | 2313s                  |

### Retrieval Performance
Results summarize the **average latency across runs** under a **concurrent workload of 20 users over 10 minutes**. Latency values are reported in **milliseconds (ms)**. (Detailed results for each run are available in separate Markdown files named after the vector database)
| Database         | P50 (ms) | P95 (ms) | P99 (ms) | Avg (ms) | RPS  |
| ---------------- | -------- | -------- | -------- | -------- | ---- |
| ChromaDB         | 66       | 120      | —        | 72       | 11.5 |
| Qdrant           | 45       | 128      | —        | 56       | 13   |
| Vespa            | 64       | 124      | —        | 72       | 12.7 |
| Weaviate         | 18       | 62       | —        | 24       | 13.5 |
| Pinecone (Local) | 3500     | —        | —        | 3500     | 4    |
| Milvus           | N/A      | N/A      | N/A      | N/A      | N/A  |

**Note**: Milvus retrieval benchmarking was not completed because loading the 1M vector collection into memory required significant startup time.

## Limitations
This benchmark has several limitations:
1. Single-machine deployment
2. No distributed scaling tested
3. Dataset size limited to **1M vectors**
4. Query recall accuracy not measured

## Folder Structure
```
vector_database_tests/
│
├── dataset/                # Vector dataset used for indexing
├── generated_queries/      # Query dataset used for retrieval benchmarking
│
├── data_processing.py      # Prepares and formats the dataset
├── data_uploading.py       # Indexing benchmark (vector insertion)
├── data_retrieval.py       # Retrieval benchmark using Locust
│
└── utils/
    ├── vespa_config/
    ├── data_storage/
    ├── chromadb_client.py
    ├── milvus_client.py
    ├── pinecone_client.py
    ├── qdrant_client.py
    ├── vespa_client.py
    └── weaviate_client.py
```

# Running the Benchmark
```
docker compose up -d --build                      # Start services
python -m vector_database_tests.data_processing   # Data preparation
python -m vector_database_tests.data_uploading    # Indexing benchmark
locust -f vector_database_tests/data_retrieval.py # Retrieval benchmark
```

## Vespa Deployment
Vespa needs its application package deployed before it can index or serve queries. Deploy `utils/vespa_config` into the running Vespa container once the service is up:
```
# Wait until the config server is healthy
curl -sf http://localhost:19071/state/v1/health

# Deploy the application package from utils/vespa_config
docker exec -it <vespa-container> vespa deploy --wait 300 /app
```