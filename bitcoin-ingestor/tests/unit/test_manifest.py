"""Unit tests for manifest generation."""

import json
import tempfile
from pathlib import Path

import pytest

from src.collector.manifest import ManifestData, create_manifest, save_manifest


def test_create_manifest():
    """Test manifest creation."""
    manifest_data = ManifestData(
        exchange="binance",
        stream="trade",
        symbol="BTCUSDT",
        instance_id="i-test-001",
        s3_key="raw/exchange=binance/stream=trade/symbol=BTCUSDT/dt=2025-01-15/14/32/part-i-test-001-1736950365123.jsonl.gz",
        record_count=1000,
        bytes_uncompressed=500000,
        bytes_gzip=250000,
        time_min_ms=1736950300000,
        time_max_ms=1736950365123,
        id_first=1000000,
        id_last=1001000,
        sha256="a" * 64,
    )

    manifest = create_manifest(manifest_data)

    assert manifest["version"] == "1"
    assert manifest["source"]["exchange"] == "binance"
    assert manifest["source"]["stream"] == "trade"
    assert manifest["source"]["symbol"] == "BTCUSDT"
    assert manifest["source"]["instance_id"] == "i-test-001"
    assert manifest["payload"]["record_count"] == 1000
    assert manifest["payload"]["bytes_uncompressed"] == 500000
    assert manifest["payload"]["bytes_gzip"] == 250000
    assert manifest["payload"]["time_min_ms"] == 1736950300000
    assert manifest["payload"]["time_max_ms"] == 1736950365123
    assert manifest["payload"]["id_first"] == 1000000
    assert manifest["payload"]["id_last"] == 1001000
    assert manifest["payload"]["sha256"] == "a" * 64
    assert "created_at_ms" in manifest
    assert isinstance(manifest["created_at_ms"], int)


def test_save_manifest():
    """Test saving manifest to file."""
    manifest = {
        "version": "1",
        "source": {
            "exchange": "binance",
            "stream": "trade",
            "symbol": "BTCUSDT",
            "instance_id": "i-test-001",
        },
        "payload": {
            "s3_key": "test.jsonl.gz",
            "record_count": 100,
            "bytes_uncompressed": 1000,
            "bytes_gzip": 500,
            "time_min_ms": 1000,
            "time_max_ms": 2000,
            "id_first": 1,
            "id_last": 100,
            "sha256": "abc123",
        },
        "created_at_ms": 1736950365123,
    }

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
        temp_path = Path(f.name)

    try:
        save_manifest(manifest, temp_path)

        assert temp_path.exists()

        # Verify contents
        with open(temp_path, "r") as f:
            loaded = json.load(f)

        assert loaded == manifest
        assert loaded["version"] == "1"
        assert loaded["payload"]["record_count"] == 100

    finally:
        if temp_path.exists():
            temp_path.unlink()


def test_manifest_schema_compliance():
    """Test that generated manifest complies with expected schema."""
    manifest_data = ManifestData(
        exchange="binance",
        stream="trade",
        symbol="BTCUSDT",
        instance_id="i-test-001",
        s3_key="test.jsonl.gz",
        record_count=100,
        bytes_uncompressed=1000,
        bytes_gzip=500,
        time_min_ms=1000,
        time_max_ms=2000,
        id_first=1,
        id_last=100,
        sha256="a" * 64,
    )

    manifest = create_manifest(manifest_data)

    # Check required top-level fields
    assert "version" in manifest
    assert "source" in manifest
    assert "payload" in manifest
    assert "created_at_ms" in manifest

    # Check source fields
    source = manifest["source"]
    assert "exchange" in source
    assert "stream" in source
    assert "symbol" in source
    assert "instance_id" in source

    # Check payload fields
    payload = manifest["payload"]
    required_payload_fields = [
        "s3_key",
        "record_count",
        "bytes_uncompressed",
        "bytes_gzip",
        "time_min_ms",
        "time_max_ms",
        "id_first",
        "id_last",
        "sha256",
    ]
    for field in required_payload_fields:
        assert field in payload

    # Check types
    assert isinstance(manifest["created_at_ms"], int)
    assert isinstance(payload["record_count"], int)
    assert isinstance(payload["bytes_uncompressed"], int)
    assert isinstance(payload["sha256"], str)
