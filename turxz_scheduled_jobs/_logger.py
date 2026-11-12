"""
Resilient logging compatibility module for TURXZ scheduled jobs.
Wraps structlog if available, or falls back to standard logging with kwarg support.
"""
import logging
from typing import Any

try:
    import structlog

    def get_logger(name: str = __name__) -> Any:
        return structlog.get_logger(name)

except ImportError:
    class _LoggerFallback:
        def __init__(self, name: str):
            self._logger = logging.getLogger(name)

        def _format_msg(self, msg: str, kwargs: dict) -> str:
            if not kwargs:
                return str(msg)
            extra = " ".join(f"{k}={v!r}" for k, v in kwargs.items())
            return f"{msg} | {extra}"

        def debug(self, msg: str, *args, **kwargs):
            self._logger.debug(self._format_msg(msg, kwargs), *args)

        def info(self, msg: str, *args, **kwargs):
            self._logger.info(self._format_msg(msg, kwargs), *args)

        def warning(self, msg: str, *args, **kwargs):
            self._logger.warning(self._format_msg(msg, kwargs), *args)

        def error(self, msg: str, *args, **kwargs):
            self._logger.error(self._format_msg(msg, kwargs), *args)

        def exception(self, msg: str, *args, **kwargs):
            self._logger.exception(self._format_msg(msg, kwargs), *args)

        def critical(self, msg: str, *args, **kwargs):
            self._logger.critical(self._format_msg(msg, kwargs), *args)

    def get_logger(name: str = __name__) -> Any:
        return _LoggerFallback(name)
