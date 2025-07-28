from demo.mongodb import mongodb_demo
from demo.elastic import elasticsearch_demo
from demo.qdrant import qdrant_demo

def all_demo():
    print("********** DEMO ALL **********")
    elasticsearch_demo()
    mongodb_demo()
    qdrant_demo()
    print("********** DONE **********") 


if __name__ == "__main__":
    all_demo()