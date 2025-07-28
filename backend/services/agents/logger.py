import logging

def startup_logger():
    logging.basicConfig(level = logging.INFO, format = "%(asctime)s - %(levelname)s - %(message)s")

def teardown_logger():
    logging.shutdown()

def get_logger():
    return logging.getLogger("my_custom_logger")
