"""File rotation and compression logic."""

import gzip
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Optional

from .manifest import ManifestData, create_manifest, save_manifest
from .s3_uploader import S3Uploader
from .utils import build_s3_key, compute_sha256, ensure_dir, get_utc_partition_path, get_utc_timestamp_ms


logger = logging.getLogger(__name__)


class RotatingWriter:
    """Manages rotating file writes with compression and S3 upload."""

    def __init__(
        self,
        local_dir: Path,
        s3_uploader: S3Uploader,
        exchange: str,
        stream: str,
        symbol: str,
        instance_id: str,
        max_bytes: int = 2_097_152,  # 2 MB
        max_seconds: float = 5.0,
    ):
        """Initialize rotating writer.

        Args:
            local_dir: Local directory for temporary files
            s3_uploader: S3 uploader instance
            exchange: Exchange name (e.g., "binance")
            stream: Stream type (e.g., "trade")
            symbol: Trading symbol (e.g., "BTCUSDT")
            instance_id: Instance identifier
            max_bytes: Maximum file size before rotation
            max_seconds: Maximum time before rotation
        """
        self.local_dir = Path(local_dir)
        self.s3_uploader = s3_uploader
        self.exchange = exchange
        self.stream = stream
        self.symbol = symbol
        self.instance_id = instance_id
        self.max_bytes = max_bytes
        self.max_seconds = max_seconds

        # Ensure local directory exists
        ensure_dir(self.local_dir)

        # Current file state
        self.current_file: Optional[Path] = None
        self.current_file_handle = None
        self.start_time: Optional[float] = None
        self.current_size: int = 0

        # Statistics for manifest
        self.record_count: int = 0
        self.time_min_ms: Optional[int] = None
        self.time_max_ms: Optional[int] = None
        self.id_first: Optional[int] = None
        self.id_last: Optional[int] = None

        logger.info(
            f"RotatingWriter initialized: max_bytes={max_bytes}, "
            f"max_seconds={max_seconds}, local_dir={local_dir}"
        )

    def _should_rotate(self) -> bool:
        """Check if rotation is needed based on size or time."""
        if self.current_file is None:
            return True

        # Check size
        if self.current_size >= self.max_bytes:
            logger.info(f"Rotation triggered by size: {self.current_size} >= {self.max_bytes}")
            return True

        # Check time
        elapsed = time.time() - self.start_time
        if elapsed >= self.max_seconds:
            logger.info(f"Rotation triggered by time: {elapsed:.2f}s >= {self.max_seconds}s")
            return True

        return False

    def _create_new_file(self) -> None:
        """Create a new file for writing."""
        timestamp_ms = get_utc_timestamp_ms()
        filename = f"part-{self.instance_id}-{timestamp_ms}.jsonl"
        self.current_file = self.local_dir / filename

        self.current_file_handle = open(self.current_file, "w")
        self.start_time = time.time()
        self.current_size = 0

        # Reset statistics
        self.record_count = 0
        self.time_min_ms = None
        self.time_max_ms = None
        self.id_first = None
        self.id_last = None

        logger.info(f"Created new file: {self.current_file}")

    def write(self, event: dict) -> None:
        """Write an event to the current file.

        Args:
            event: Event dictionary to write
        """
        # Check if rotation is needed
        if self._should_rotate():
            self._rotate()

        # Create new file if needed
        if self.current_file is None:
            self._create_new_file()

        # Write event as JSON line
        line = json.dumps(event) + "\n"
        self.current_file_handle.write(line)
        self.current_size += len(line.encode("utf-8"))

        # Update statistics
        self.record_count += 1

        # Extract timestamp (Binance trade events have "T" field for timestamp)
        if "T" in event:
            event_time = event["T"]
            if self.time_min_ms is None or event_time < self.time_min_ms:
                self.time_min_ms = event_time
            if self.time_max_ms is None or event_time > self.time_max_ms:
                self.time_max_ms = event_time

        # Extract trade ID (Binance trade events have "t" field for trade ID)
        if "t" in event:
            trade_id = event["t"]
            if self.id_first is None:
                self.id_first = trade_id
            self.id_last = trade_id

    def _rotate(self) -> None:
        """Rotate current file: compress, upload, create manifest."""
        if self.current_file is None or self.record_count == 0:
            logger.info("No data to rotate")
            return

        # Close current file
        self.current_file_handle.close()

        logger.info(f"Rotating file: {self.current_file} ({self.record_count} records)")

        try:
            # Compress file
            gzip_file = self._compress_file(self.current_file)

            # Upload to S3
            success = self._upload_to_s3(gzip_file)

            if success:
                # Clean up local files
                self.current_file.unlink()
                gzip_file.unlink()
                logger.info(f"Successfully rotated and cleaned up {self.current_file.name}")
            else:
                logger.error(f"Failed to upload {gzip_file}, keeping local files")

        except Exception as e:
            logger.error(f"Error during rotation: {e}")

        # Reset current file
        self.current_file = None
        self.current_file_handle = None

    def _compress_file(self, file_path: Path) -> Path:
        """Compress file using gzip.

        Args:
            file_path: Path to file to compress

        Returns:
            Path to compressed file
        """
        gzip_path = file_path.with_suffix(file_path.suffix + ".gz")

        logger.info(f"Compressing {file_path} -> {gzip_path}")

        with open(file_path, "rb") as f_in:
            with gzip.open(gzip_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

        original_size = file_path.stat().st_size
        compressed_size = gzip_path.stat().st_size
        ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0

        logger.info(
            f"Compression complete: {original_size} -> {compressed_size} bytes "
            f"({ratio:.1f}% reduction)"
        )

        return gzip_path

    def _upload_to_s3(self, gzip_file: Path) -> bool:
        """Upload compressed file and manifest to S3.

        Args:
            gzip_file: Path to compressed file

        Returns:
            True if successful, False otherwise
        """
        # Build S3 key
        partition_path = get_utc_partition_path(self.time_min_ms or get_utc_timestamp_ms())
        s3_key = build_s3_key(
            self.exchange,
            self.stream,
            self.symbol,
            partition_path,
            gzip_file.name,
        )

        # Upload data file
        if not self.s3_uploader.upload_file(gzip_file, s3_key):
            return False

        # Create and upload manifest
        manifest_data = ManifestData(
            exchange=self.exchange,
            stream=self.stream,
            symbol=self.symbol,
            instance_id=self.instance_id,
            s3_key=s3_key,
            record_count=self.record_count,
            bytes_uncompressed=self.current_file.stat().st_size,
            bytes_gzip=gzip_file.stat().st_size,
            time_min_ms=self.time_min_ms or 0,
            time_max_ms=self.time_max_ms or 0,
            id_first=self.id_first or 0,
            id_last=self.id_last or 0,
            sha256=compute_sha256(gzip_file),
        )

        manifest = create_manifest(manifest_data)

        # Upload manifest
        manifest_filename = f"manifest-{self.instance_id}-{self.time_min_ms or get_utc_timestamp_ms()}.json"
        manifest_s3_key = build_s3_key(
            self.exchange,
            self.stream,
            self.symbol,
            partition_path,
            manifest_filename,
        )

        return self.s3_uploader.upload_json(manifest, manifest_s3_key)

    def flush(self) -> None:
        """Force rotation of current file."""
        logger.info("Forcing flush/rotation")
        self._rotate()

    def close(self) -> None:
        """Close writer and flush any remaining data."""
        logger.info("Closing RotatingWriter")
        if self.current_file_handle:
            self.flush()
