"""
Simple logging utilities for DeepSeek Web Bridge Backend.
"""

import os
import logging
from typing import Optional, Dict, Any

# Configure logging based on environment
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
service_name = os.getenv("LOG_SERVICE_NAME", "deepseek-web-bridge-backend")

# Create logger
logger = logging.getLogger(service_name)
logger.setLevel(getattr(logging, log_level, logging.INFO))

# Configure handlers if not already configured
if not logger.handlers:
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level, logging.INFO))
    
    # Simple formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    
    # Add handler
    logger.addHandler(console_handler)

# Simple convenience functions
def log_info(msg: str, **kwargs):
    """Log info message."""
    logger.info(msg, extra=kwargs)

def log_error(error: Exception, context: Optional[Dict[str, Any]] = None) -> None:
    """Log error with context."""
    logger.error(
        str(error),
        exc_info=True,
        extra={
            "error": str(error),
            "error_type": type(error).__name__,
            "context": context or {},
        }
    )

def log_metric(name: str, value: float, labels: Optional[Dict[str, str]] = None) -> None:
    """Log metric for monitoring."""
    logger.info(
        "metric",
        extra={
            "name": name,
            "value": value,
            "labels": labels or {},
        }
    )

# Initialize logger
logger.info(f"Logger initialized for {service_name}")

# Export functions
__all__ = ["log_info", "log_error", "log_metric", "logger"]
