"""日志：控制台 + 滚动文件，方便答辩时回溯整条链路。"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LOG_DIR

_configured = False
LOG_FILE = LOG_DIR / "assistant.log"


def setup_logging(level: int = logging.INFO) -> Path:
    global _configured
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if not _configured:
        root.setLevel(level)
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)-18s | %(message)s",
            datefmt="%H:%M:%S",
        )
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(fmt)
        root.addHandler(file_handler)
        root.addHandler(stream_handler)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        _configured = True
    return LOG_FILE


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
