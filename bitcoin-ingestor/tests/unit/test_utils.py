"""Unit tests for utility functions."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.collector.utils import (
    build_s3_key,
    compute_sha256,
    get_utc_partition_path,
    get_utc_timestamp_ms,
)


def test_get_utc_timestamp_ms():
    """Test UTC timestamp generation."""
    ts = get_utc_timestamp_ms()
    assert isinstance(ts, int)
    assert ts > 0
    assert ts > 1700000000000  # After 2023


def test_get_utc_partition_path():
    """Test partition path generation."""
    # 2025-01-15 14:32:45.123 UTC
    timestamp_ms = 1736950365123

    path = get_utc_partition_path(timestamp_ms)

    assert path == "dt=2025-01-15/14/32"
    assert path.startswith("dt=")


def test_compute_sha256():
    """Test SHA256 hash computation."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("test content\n")
        temp_path = Path(f.name)

    try:
        hash_value = compute_sha256(temp_path)

        assert isinstance(hash_value, str)
        assert len(hash_value) == 64  # SHA256 is 64 hex characters
        assert all(c in "0123456789abcdef" for c in hash_value)

        # Verify deterministic
        hash_value2 = compute_sha256(temp_path)
        assert hash_value == hash_value2

    finally:
        temp_path.unlink()


def test_build_s3_key():
    """Test S3 key construction."""
    key = build_s3_key(
        exchange="binance",
        stream="trade",
        symbol="BTCUSDT",
        partition_path="dt=2025-01-15/14/32",
        filename="part-i-001-1736950365123.jsonl.gz",
    )

    expected = (
        "raw/exchange=binance/stream=trade/symbol=BTCUSDT/"
        "dt=2025-01-15/14/32/part-i-001-1736950365123.jsonl.gz"
    )

    assert key == expected
    assert key.startswith("raw/")
    assert "exchange=binance" in key
    assert "stream=trade" in key
    assert "symbol=BTCUSDT" in key


def test_build_s3_key_special_characters():
    """Test S3 key with special characters in symbol."""
    key = build_s3_key(
        exchange="binance",
        stream="trade",
        symbol="BTC-USDT",
        partition_path="dt=2025-01-15/14/32",
        filename="test.gz",
    )

    assert "symbol=BTC-USDT" in key
