"""Manifest generation for uploaded data files."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import compute_sha256, get_utc_timestamp_ms


@dataclass
class ManifestData:
    """Data structure for manifest metadata."""

    # Source information
    exchange: str
    stream: str
    symbol: str
    instance_id: str

    # Payload information
    s3_key: str
    record_count: int
    bytes_uncompressed: int
    bytes_gzip: int
    time_min_ms: int
    time_max_ms: int
    id_first: int
    id_last: int
    sha256: str


def create_manifest(
    manifest_data: ManifestData,
) -> dict[str, Any]:
    """Create manifest JSON structure.

    Args:
        manifest_data: Manifest metadata

    Returns:
        Manifest dictionary
    """
    return {
        "version": "1",
        "source": {
            "exchange": manifest_data.exchange,
            "stream": manifest_data.stream,
            "symbol": manifest_data.symbol,
            "instance_id": manifest_data.instance_id,
        },
        "payload": {
            "s3_key": manifest_data.s3_key,
            "record_count": manifest_data.record_count,
            "bytes_uncompressed": manifest_data.bytes_uncompressed,
            "bytes_gzip": manifest_data.bytes_gzip,
            "time_min_ms": manifest_data.time_min_ms,
            "time_max_ms": manifest_data.time_max_ms,
            "id_first": manifest_data.id_first,
            "id_last": manifest_data.id_last,
            "sha256": manifest_data.sha256,
        },
        "created_at_ms": get_utc_timestamp_ms(),
    }


def save_manifest(manifest: dict[str, Any], file_path: Path) -> None:
    """Save manifest to JSON file.

    Args:
        manifest: Manifest dictionary
        file_path: Path to save the manifest
    """
    with open(file_path, "w") as f:
        json.dump(manifest, f, indent=2)


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA256 hash for a file.

    Args:
        file_path: Path to the file

    Returns:
        SHA256 hash string
    """
    return compute_sha256(file_path)
