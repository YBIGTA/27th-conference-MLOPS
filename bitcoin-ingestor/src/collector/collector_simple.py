"""Simple market data collector - main entry point."""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

from .rotator import RotatingWriter
from .s3_uploader import S3Uploader
from .utils import ensure_dir, setup_logging
from .ws_client import BinanceWSClient


# Load environment variables
load_dotenv()

# Global flag for graceful shutdown
shutdown_flag = False
logger = logging.getLogger(__name__)


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    global shutdown_flag
    if logger.hasHandlers():
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_flag = True


async def main():
    """Main async entry point."""
    global logger

    # Set up logging
    logger = setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    logger.info("Starting market data collector")

    # Load configuration from environment
    raw_bucket = os.getenv("RAW_BUCKET")
    symbol = os.getenv("SYMBOL", "BTCUSDT")
    local_dir = Path(os.getenv("LOCAL_DIR", "/tmp/market-data"))
    rot_bytes = int(os.getenv("ROT_BYTES", "2097152"))
    rot_secs = float(os.getenv("ROT_SECS", "5"))
    instance_id = os.getenv("INSTANCE_ID", "local-001")
    aws_region = os.getenv("AWS_REGION")

    # Validate required configuration
    if not raw_bucket:
        logger.error("RAW_BUCKET environment variable is required")
        sys.exit(1)

    logger.info(f"Configuration:")
    logger.info(f"  RAW_BUCKET: {raw_bucket}")
    logger.info(f"  SYMBOL: {symbol}")
    logger.info(f"  LOCAL_DIR: {local_dir}")
    logger.info(f"  ROT_BYTES: {rot_bytes}")
    logger.info(f"  ROT_SECS: {rot_secs}")
    logger.info(f"  INSTANCE_ID: {instance_id}")
    logger.info(f"  AWS_REGION: {aws_region or 'default'}")

    # Ensure local directory exists
    ensure_dir(local_dir)

    # Initialize S3 uploader
    s3_uploader = S3Uploader(
        bucket_name=raw_bucket,
        region=aws_region,
        max_retries=3,
        retry_backoff_base=2.0,
    )

    # Initialize rotating writer
    writer = RotatingWriter(
        local_dir=local_dir,
        s3_uploader=s3_uploader,
        exchange="binance",
        stream="trade",
        symbol=symbol,
        instance_id=instance_id,
        max_bytes=rot_bytes,
        max_seconds=rot_secs,
    )

    # Initialize WebSocket client
    ws_client = BinanceWSClient(
        symbol=symbol,
        stream_type="trade",
        reconnect_delay=1.0,
        ping_interval=20.0,
        ping_timeout=10.0,
    )

    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Message handler
    def handle_message(event: dict):
        """Handle incoming trade event."""
        if shutdown_flag:
            return

        try:
            writer.write(event)
        except Exception as e:
            logger.error(f"Error writing event: {e}")

    # Start streaming
    logger.info("Starting WebSocket stream...")
    try:
        await ws_client.connect_and_stream(handle_message)
    except asyncio.CancelledError:
        logger.info("Stream cancelled")
    except Exception as e:
        logger.error(f"Unexpected error in stream: {e}")
    finally:
        # Clean up
        logger.info("Shutting down...")
        writer.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)
