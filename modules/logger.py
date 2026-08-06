"""Jarvis project-wide logger setup."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler


def setup_logger(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    
    fh = RotatingFileHandler(log_dir / "jarvis.log", maxBytes=20_000_00, backupCount=5, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(fh)
    root.addHandler(ch)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
