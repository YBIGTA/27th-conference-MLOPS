"""Utility functions for the market data collector."""

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Set up logging configuration."""
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    return logging.getLogger("collector")


def get_utc_timestamp_ms() -> int:
    """Get current UTC timestamp in milliseconds."""
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def get_utc_partition_path(timestamp_ms: int) -> str:
    """Generate UTC-based partition path from timestamp.

    Args:
        timestamp_ms: Timestamp in milliseconds

    Returns:
        Path string like "dt=YYYY-MM-DD/HH/MM"
    """
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return f"dt={dt.strftime('%Y-%m-%d')}/{dt.strftime('%H')}/{dt.strftime('%M')}"


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file.

    Args:
        file_path: Path to the file

    Returns:
        Hexadecimal SHA256 hash string
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def ensure_dir(path: Path) -> None:
    """Ensure directory exists, create if it doesn't."""
    path.mkdir(parents=True, exist_ok=True)


def build_s3_key(
    exchange: str,
    stream: str,
    symbol: str,
    partition_path: str,
    filename: str,
) -> str:
    """Build S3 key following the specified layout.

    Args:
        exchange: Exchange name (e.g., "binance")
        stream: Stream type (e.g., "trade")
        symbol: Trading symbol (e.g., "BTCUSDT")
        partition_path: Partition path (e.g., "dt=2025-01-01/12/34")
        filename: File name

    Returns:
        Full S3 key string
    """
    return f"raw/exchange={exchange}/stream={stream}/symbol={symbol}/{partition_path}/{filename}"
