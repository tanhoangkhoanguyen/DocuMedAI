from datetime import datetime
from pathlib import Path

import logging

_logger_dict = {}

class SimpleLogger:
    """
    general-purpose logger
    """

    def __init__(
            self, 
            name: str = "app", 
            level: str = "INFO"
        ):
        self.name = name
        self.level = getattr(logging, level.upper())
        self.logs_dir = Path("logs")
        self.logs_dir.mkdir(exist_ok=True)
        self._setup_logging()

    def _setup_logging(self):
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(self.level)

        # avoid duplicate handlers
        for h in self.logger.handlers[:]:
            self.logger.removeHandler(h)

        log_file = self.logs_dir / f"{self.name}_{datetime.now().strftime('%Y%m%d')}.log"

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(self.level)

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
        )
        file_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)

    # -------- basic logs --------
    def info(self, msg, **kwargs):
        self.logger.info(msg, **kwargs)

    def warning(self, msg, **kwargs):
        self.logger.warning(msg, **kwargs)

    def error(self, msg, **kwargs):
        self.logger.error(msg, exc_info=True, **kwargs)

    def debug(self, msg, **kwargs):
        self.logger.debug(msg, **kwargs)

def get_logger(
        name: str = "app", 
        level: str = "INFO"
    ):
    if name in _logger_dict:
        return _logger_dict[name]

    logger = SimpleLogger(
        name=name, 
        level=level
    ).logger
    _logger_dict[name] = logger
    return logger