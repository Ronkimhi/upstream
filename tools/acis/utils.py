"""Shared utilities: rate limiting, logging setup."""
import logging
import time


def setup_logger(name, verbose=False):
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        fmt = "%(asctime)s [%(levelname)s] %(message)s"
        handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
        logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    return logger


class RateLimiter:
    def __init__(self, delay_seconds=0.5):
        self.delay = delay_seconds
        self.last_call = 0.0
        self.call_count = 0

    def wait(self):
        elapsed = time.time() - self.last_call
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self.last_call = time.time()
        self.call_count += 1
