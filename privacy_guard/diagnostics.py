"""Value-free backend diagnostics; never record arguments or exception messages."""

import asyncio
import json
import logging
import os
import time
import traceback
from contextvars import ContextVar
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path

request_id = ContextVar("request_id", default="-")
task_id = ContextVar("task_id", default="-")
logger = logging.getLogger("privacy_guard")


class DiagnosticFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + "Z",
            "level": record.levelname,
            "request_id": request_id.get(),
            "task_id": task_id.get(),
            "event": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            exc = record.exc_info[1]
            entry["error_type"] = type(exc).__name__
            entry["stack"] = [
                {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(record.exc_info[2])
            ]
        return json.dumps(entry)

    converter = time.gmtime


def configure_logging(data_dir: Path):
    level = os.environ.get("GUARD_LOG_LEVEL", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("GUARD_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    directory = data_dir / "logs"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "backend.log"
    # The entry point sets a restrictive umask; explicitly protect existing files too.
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    for handler in list(logger.handlers):
        if getattr(handler, "guard_diagnostics", False):
            logger.removeHandler(handler)
            handler.close()
    for handler in (logging.StreamHandler(), RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3)):
        handler.guard_diagnostics = True
        handler.setFormatter(DiagnosticFormatter())
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    logger.info("logging.ready")
    return path


def traced(operation):
    """Trace an async boundary using only a static operation name and elapsed time."""
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            started = time.monotonic()
            logger.info("%s.started", operation)
            try:
                result = await function(*args, **kwargs)
            except asyncio.CancelledError:
                logger.info("%s.cancelled", operation)
                raise
            except Exception:
                logger.error("%s.failed duration_ms=%.1f", operation,
                             (time.monotonic() - started) * 1000, exc_info=True)
                raise
            logger.info("%s.completed duration_ms=%.1f", operation,
                        (time.monotonic() - started) * 1000)
            return result
        return wrapped
    return decorate
