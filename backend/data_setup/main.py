from data_setup.qdrant import qdrant_setup
from data_setup.elastic import elastic_setup

if __name__ == "__main__":
    qdrant_setup()
    elastic_setup()