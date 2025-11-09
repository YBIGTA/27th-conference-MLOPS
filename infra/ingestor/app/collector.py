import asyncio
import json
import logging
import os
import signal
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
import pandas as pd
from dotenv import load_dotenv
from tenacity import RetryError, retry, stop_after_delay, wait_exponential
import websockets


load_dotenv()


def _get_env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable '{name}'")
    return value


@dataclass
class Config:
    region: str
    bucket: str
    prefix: str
    ws_url: str
    trading_pair: str
    batch_max_trades: int
    batch_max_seconds: int
    batch_max_bytes: int
    file_format: str
    data_source: str
    asset_symbol: str

    @classmethod
    def from_env(cls) -> "Config":
        data_source = os.getenv("DATA_SOURCE", "Binance")
        asset_symbol = os.getenv("ASSET_SYMBOL", "BTCUSDT")
        prefix = os.getenv("S3_PREFIX")
        if not prefix:
            prefix = f"{data_source}/{asset_symbol}/"
        return cls(
            region=_get_env("AWS_REGION"),
            bucket=_get_env("S3_BUCKET"),
            prefix=prefix,
            ws_url=_get_env("EXCHANGE_WS_URL", "wss://stream.binance.com:9443/ws"),
            trading_pair=os.getenv("TRADING_PAIR", "btcusdt"),
            batch_max_trades=int(os.getenv("BATCH_MAX_TRADES", "5000")),
            batch_max_seconds=int(os.getenv("BATCH_MAX_SECONDS", "15")),
            batch_max_bytes=int(os.getenv("BATCH_MAX_BYTES", str(2 * 1024 * 1024))),
            file_format=os.getenv("FILE_FORMAT", "parquet").lower(),
            data_source=data_source,
            asset_symbol=asset_symbol,
        )

    def build_stream_url(self) -> str:
        sanitized = self.ws_url.rstrip("/")
        if self.trading_pair in sanitized:
            return sanitized
        return f"{sanitized}/{self.trading_pair}@trade"


class TradeBuffer:
    """Aggregates trades until size, byte, or time threshold is reached."""

    def __init__(self, max_trades: int, max_seconds: int, max_bytes: int) -> None:
        self.max_trades = max_trades
        self.max_seconds = max_seconds
        self.max_bytes = max_bytes
        self.records: List[Dict[str, Any]] = []
        self.window_start: Optional[datetime] = None
        self.byte_count: int = 0

    def add(self, trade: Dict[str, Any]) -> bool:
        if not self.records:
            self.window_start = datetime.now(timezone.utc)
        self.records.append(trade)
        approx = len(json.dumps(trade, separators=(",", ":")).encode("utf-8"))
        self.byte_count += approx
        return self.should_flush()

    def should_flush(self) -> bool:
        if not self.records:
            return False
        if len(self.records) >= self.max_trades:
            return True
        if self.byte_count >= self.max_bytes:
            return True
        assert self.window_start is not None
        elapsed = (datetime.now(timezone.utc) - self.window_start).total_seconds()
        return elapsed >= self.max_seconds

    def flush(self) -> Dict[str, Any]:
        if not self.records:
            return {
                "records": [],
                "window_start": self.window_start,
                "window_end": None,
                "byte_count": 0,
            }
        window_start = self.window_start or datetime.now(timezone.utc)
        payload = {
            "records": self.records[:],
            "window_start": window_start,
            "window_end": datetime.now(timezone.utc),
            "byte_count": self.byte_count,
        }
        self.records.clear()
        self.window_start = None
        self.byte_count = 0
        return payload


class TradeBatchWriter:
    def __init__(self, client, bucket: str, prefix: str, fmt: str = "parquet") -> None:
        self.client = client
        self.bucket = bucket
        sanitized = prefix.rstrip("/") if prefix else ""
        self.prefix = f"{sanitized}/" if sanitized else ""
        if fmt not in {"parquet", "csv"}:
            raise ValueError("FILE_FORMAT must be 'parquet' or 'csv'")
        self.fmt = fmt

    def _build_key(self, timestamp: datetime) -> str:
        ts = timestamp.astimezone(timezone.utc)
        path = ts.strftime("%Y/%m/%d/%H/%M")
        extension = "parquet" if self.fmt == "parquet" else "csv.gz"
        return f"{self.prefix}{path}/batch-{uuid.uuid4().hex}.{extension}"

    def write(self, records: List[Dict[str, Any]], window_start: datetime) -> str:
        df = pd.DataFrame.from_records(records)
        if df.empty:
            raise ValueError("Attempted to persist empty batch")
        key = self._build_key(window_start)
        suffix = ".parquet" if self.fmt == "parquet" else ".csv.gz"

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            path = Path(tmp.name)
        try:
            if self.fmt == "parquet":
                df.to_parquet(path, index=False)
            else:
                df.to_csv(path, index=False, compression="gzip")
            self.client.upload_file(str(path), self.bucket, key)
        finally:
            path.unlink(missing_ok=True)
        return key


def normalize_trade(message: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a raw Binance trade payload into our schema."""
    return {
        "event_time": datetime.fromtimestamp(message["E"] / 1000, tz=timezone.utc).isoformat(),
        "trade_time": datetime.fromtimestamp(message["T"] / 1000, tz=timezone.utc).isoformat(),
        "symbol": message["s"],
        "trade_id": message["t"],
        "price": float(message["p"]),
        "quantity": float(message["q"]),
        "buyer_order_id": message.get("b"),
        "seller_order_id": message.get("a"),
        "is_market_maker": message.get("m", False),
    }


async def collect(config: Config) -> None:
    logging.info("Starting collector with %s", config)
    session = boto3.session.Session(region_name=config.region)
    s3 = session.client("s3")
    buffer = TradeBuffer(config.batch_max_trades, config.batch_max_seconds, config.batch_max_bytes)
    writer = TradeBatchWriter(s3, config.bucket, config.prefix, config.file_format)
    stop_event = asyncio.Event()

    def _handle_stop(*_):
        logging.warning("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handle_stop)

    stream_url = config.build_stream_url()

    @retry(wait=wait_exponential(multiplier=1, min=1, max=30), stop=stop_after_delay(3600))
    async def _run_stream() -> None:
        async with websockets.connect(stream_url, ping_interval=20, ping_timeout=20) as ws:
            logging.info("Connected to %s", stream_url)
            while not stop_event.is_set():
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
                message = json.loads(raw)
                if "result" in message:
                    continue
                trade = normalize_trade(message)
                if buffer.add(trade):
                    await flush(buffer, writer, config)

    while not stop_event.is_set():
        try:
            await _run_stream()
        except (asyncio.TimeoutError, websockets.ConnectionClosed) as exc:
            logging.warning("WebSocket issue (%s), reconnecting...", exc)
            await asyncio.sleep(1)
        except RetryError as exc:
            logging.error("Retry budget exhausted: %s", exc)
            raise
        except Exception:
            logging.exception("Unexpected error inside stream loop; restarting in 5s")
            await asyncio.sleep(5)

    if buffer.records:
        await flush(buffer, writer, config)
    logging.info("Collector stopped cleanly")


async def flush(buffer: TradeBuffer, writer: TradeBatchWriter, config: Config) -> None:
    payload = buffer.flush()
    records = payload["records"]
    if not records:
        return
    window_start = payload["window_start"]
    window_end = payload["window_end"]
    byte_count = payload["byte_count"]
    assert isinstance(window_start, datetime) and isinstance(window_end, datetime)

    logging.info(
        "Flushing %s trades (~%s bytes) accumulated between %s and %s",
        len(records),
        byte_count,
        window_start.isoformat(),
        window_end.isoformat(),
    )
    key = writer.write(records, window_start)
    logging.info("Uploaded batch %s to %s (records=%s, approx_bytes=%s)", key, config.bucket, len(records), byte_count)


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )


def main() -> None:
    configure_logging()
    config = Config.from_env()
    try:
        asyncio.run(collect(config))
    except KeyboardInterrupt:
        logging.info("Interrupted")


if __name__ == "__main__":
    main()
