from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


_CONFIGURED = False


def setup_logging(working_directory: str, debug: bool = False) -> None:
    """
    Configure Zeno logging.

    - File handler: DEBUG level → <wd>/.zeno/logs/zeno.log (rotating)
    - Console handler: WARNING+ only by default (DEBUG if `debug=True`)
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir = Path(working_directory).resolve() / ".zeno" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "zeno.log"

    root_logger = logging.getLogger("zeno")
    root_logger.setLevel(logging.DEBUG)

    # Avoid duplicating handlers if setup is called twice in-process.
    for h in list(root_logger.handlers):
        if isinstance(h, logging.handlers.RotatingFileHandler) and getattr(h, "baseFilename", "") == str(log_file):
            _CONFIGURED = True
            return

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if debug else logging.WARNING)
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Suppress noisy third-party loggers.
    for noisy in ("httpx", "httpcore", "chromadb", "anyio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True

