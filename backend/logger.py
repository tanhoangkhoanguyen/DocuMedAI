import logging
logging.getLogger().setLevel(logging.WARNING)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("[%(levelname)s] [%(filename)s] %(message)s"))
        logger.addHandler(handler)
    return logger