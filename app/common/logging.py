import sys

from loguru import logger

from .settings import get_settings


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=get_settings().log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>",
    )


__all__ = ["setup_logging", "logger"]
