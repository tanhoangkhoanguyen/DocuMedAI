from data_setup.qdrant_setup import QdrantSetup
from data_setup.elastic_setup import ElasticSetup
from data_setup.mongodb_setup import MongoDBSetup

if __name__ == "__main__":
    elastic_configuration = ElasticSetup()
    elastic_configuration.elastic_setup()
    mongo_configuration = MongoDBSetup()
    mongo_configuration.mongo_setup()
    qdrant_configuration = QdrantSetup()
    qdrant_configuration.qdrant_setup()