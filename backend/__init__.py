"""
DeepSeek Web Bridge Backend - Main package.

This package provides Python backend services for TURZX display integration
with agent quota management, complementing the existing JavaScript implementation
of semantic signature verification.
"""

try:
    from .api.main import create_app
except ImportError:
    create_app = None  # type: ignore

try:
    from .settings import get_settings
    from .utils.logger import log_info
except ImportError:
    get_settings = None  # type: ignore
    log_info = None  # type: ignore

__all__ = [
    "create_app",
    "get_settings",
    "log_info",
]

__version__ = "1.0.0"
