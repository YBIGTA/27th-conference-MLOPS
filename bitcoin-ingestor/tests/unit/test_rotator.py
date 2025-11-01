"""Unit tests for rotating writer."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from src.collector.rotator import RotatingWriter
from src.collector.s3_uploader import S3Uploader


@pytest.fixture
def temp_dir():
    """Create temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_s3_uploader():
    """Create mock S3 uploader."""
    mock = Mock(spec=S3Uploader)
    mock.upload_file.return_value = True
    mock.upload_json.return_value = True
    return mock


def test_writer_initialization(temp_dir, mock_s3_uploader):
    """Test writer initialization."""
    writer = RotatingWriter(
        local_dir=temp_dir,
        s3_uploader=mock_s3_uploader,
        exchange="binance",
        stream="trade",
        symbol="BTCUSDT",
        instance_id="test-001",
        max_bytes=1000,
        max_seconds=5.0,
    )

    assert writer.local_dir == temp_dir
    assert writer.exchange == "binance"
    assert writer.stream == "trade"
    assert writer.symbol == "BTCUSDT"
    assert writer.instance_id == "test-001"
    assert writer.max_bytes == 1000
    assert writer.max_seconds == 5.0
    assert writer.current_file is None


def test_write_creates_file(temp_dir, mock_s3_uploader):
    """Test that write creates a file."""
    writer = RotatingWriter(
        local_dir=temp_dir,
        s3_uploader=mock_s3_uploader,
        exchange="binance",
        stream="trade",
        symbol="BTCUSDT",
        instance_id="test-001",
        max_bytes=1000000,
        max_seconds=100.0,
    )

    event = {
        "e": "trade",
        "E": 1736950365123,
        "s": "BTCUSDT",
        "t": 1000000,
        "p": "42000.50",
        "q": "0.1",
        "T": 1736950365123,
        "m": True,
    }

    writer.write(event)

    assert writer.current_file is not None
    assert writer.current_file.exists()
    assert writer.record_count == 1


def test_write_updates_statistics(temp_dir, mock_s3_uploader):
    """Test that write updates statistics correctly."""
    writer = RotatingWriter(
        local_dir=temp_dir,
        s3_uploader=mock_s3_uploader,
        exchange="binance",
        stream="trade",
        symbol="BTCUSDT",
        instance_id="test-001",
        max_bytes=1000000,
        max_seconds=100.0,
    )

    events = [
        {"t": 1000, "T": 1000, "e": "trade"},
        {"t": 1001, "T": 1500, "e": "trade"},
        {"t": 1002, "T": 2000, "e": "trade"},
    ]

    for event in events:
        writer.write(event)

    assert writer.record_count == 3
    assert writer.time_min_ms == 1000
    assert writer.time_max_ms == 2000
    assert writer.id_first == 1000
    assert writer.id_last == 1002


def test_rotation_on_size(temp_dir, mock_s3_uploader):
    """Test that rotation triggers on file size."""
    writer = RotatingWriter(
        local_dir=temp_dir,
        s3_uploader=mock_s3_uploader,
        exchange="binance",
        stream="trade",
        symbol="BTCUSDT",
        instance_id="test-001",
        max_bytes=200,  # Small size to trigger rotation
        max_seconds=100.0,
    )

    # Write enough data to exceed size threshold
    for i in range(10):
        event = {
            "e": "trade",
            "t": 1000000 + i,
            "T": 1736950365123 + i,
            "p": "42000.50",
            "q": "0.1",
            "m": True,
        }
        writer.write(event)

    # Should have triggered upload
    assert mock_s3_uploader.upload_file.called
    assert mock_s3_uploader.upload_json.called


def test_file_naming_convention(temp_dir, mock_s3_uploader):
    """Test file naming follows convention."""
    writer = RotatingWriter(
        local_dir=temp_dir,
        s3_uploader=mock_s3_uploader,
        exchange="binance",
        stream="trade",
        symbol="BTCUSDT",
        instance_id="i-abc123",
        max_bytes=1000000,
        max_seconds=100.0,
    )

    event = {"t": 1000, "T": 1000, "e": "trade"}
    writer.write(event)

    filename = writer.current_file.name
    assert filename.startswith("part-i-abc123-")
    assert filename.endswith(".jsonl")


def test_close_flushes_data(temp_dir, mock_s3_uploader):
    """Test that close flushes remaining data."""
    writer = RotatingWriter(
        local_dir=temp_dir,
        s3_uploader=mock_s3_uploader,
        exchange="binance",
        stream="trade",
        symbol="BTCUSDT",
        instance_id="test-001",
        max_bytes=1000000,
        max_seconds=100.0,
    )

    event = {"t": 1000, "T": 1000, "e": "trade"}
    writer.write(event)

    writer.close()

    # Should have uploaded on close
    assert mock_s3_uploader.upload_file.called
    assert mock_s3_uploader.upload_json.called
