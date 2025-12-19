#!/usr/bin/env python3
"""
Utility functions for Dracma API operations
"""
version = 1
print(f"utils version: {version}")

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict



class JSONFormatter(logging.Formatter):
    """Custom JSON formatter for AWS CloudWatch logs"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields if present
        if hasattr(record, "extra"):
            log_data.update(record.extra)
        
        return json.dumps(log_data, ensure_ascii=False)


def load_env_file(env_path: str = ".env", override: bool = False) -> None:
    """
    Load environment variables from a .env file if present.
    """
    env_file = Path(env_path)
    if not env_file.exists():
        return

    for line in env_file.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].strip()
        if "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if not override and key in os.environ:
            continue

        os.environ[key] = value


def setup_logger(name: str = "dracma", level: str = "INFO") -> logging.Logger:
    """
    Set up logger with JSON formatting for AWS infrastructure
    
    Args:
        name: Logger name
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()
    
    # Create console handler (stdout for AWS Lambda/CloudWatch)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    
    # Set JSON formatter
    formatter = JSONFormatter()
    handler.setFormatter(formatter)
    
    logger.addHandler(handler)
    
    # Prevent propagation to root logger
    logger.propagate = False
    
    return logger


def get_logger(name: str = "dracma") -> logging.Logger:
    """Get or create logger instance"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        # Set up logger if not already configured
        log_level = os.getenv("LOG_LEVEL", "INFO")
        return setup_logger(name, log_level)
    return logger


logger = get_logger("dracma.utils")
