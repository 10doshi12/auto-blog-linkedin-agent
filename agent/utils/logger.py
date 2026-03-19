import logging
import sys


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger that writes to stdout with a consistent format.
    GitHub Actions captures stdout per step, so this shows up cleanly in run logs.

    Usage:
        from agent.utils.logger import get_logger
        logger = get_logger(__name__)
        logger.info("Fetching repos...")
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        # Already configured — return as-is to avoid duplicate handlers
        # when the same module is imported multiple times
        return logger

    logger.setLevel(logging.DEBUG)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Prevent log records bubbling up to the root logger
    logger.propagate = False

    return logger