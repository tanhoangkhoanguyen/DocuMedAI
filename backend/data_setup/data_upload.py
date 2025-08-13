from data_setup.qdrant_setup import QdrantSetup
from data_setup.elastic_setup import ElasticSetup

if __name__ == "__main__":
    elastic_configuration = ElasticSetup()
    elastic_configuration.elastic_setup()
    qdrant_configuration = QdrantSetup()
    qdrant_configuration.qdrant_setup()