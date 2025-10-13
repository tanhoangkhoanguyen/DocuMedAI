from backend.playground.khoanth.chatbot.core.main import embedding_model
from data_setup.qdrant_setup import QdrantSetup
from data_setup.elastic_setup import ElasticSetup
from data_setup.mongodb_setup import MongoDBSetup

if __name__ == "__main__":
    elastic_configuration = ElasticSetup(chunk_size = 2400)
    elastic_configuration.elastic_setup()

    mongo_configuration = MongoDBSetup()
    mongo_configuration.mongo_setup()

    embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
    qdrant_configuration = QdrantSetup(embedding_model = embedding_model)
    qdrant_configuration.qdrant_setup()