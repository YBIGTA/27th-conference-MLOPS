"""Market data collector package."""

from .manifest import ManifestData, create_manifest, save_manifest
from .rotator import RotatingWriter
from .s3_uploader import S3Uploader
from .utils import (
    build_s3_key,
    compute_sha256,
    ensure_dir,
    get_utc_partition_path,
    get_utc_timestamp_ms,
    setup_logging,
)
from .ws_client import BinanceWSClient

__all__ = [
    "BinanceWSClient",
    "ManifestData",
    "RotatingWriter",
    "S3Uploader",
    "build_s3_key",
    "compute_sha256",
    "create_manifest",
    "ensure_dir",
    "get_utc_partition_path",
    "get_utc_timestamp_ms",
    "save_manifest",
    "setup_logging",
]
