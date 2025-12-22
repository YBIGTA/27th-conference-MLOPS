"""
Shared utilities for loading Binance BTCUSDT ticks from S3 and writing 1-second
OHLCV bars into Postgres/Supabase.

Environment variables (placeholders):
- POSTGRES_URL or DATABASE_URL (preferred DSN, e.g., postgresql://user:pass@host:5432/db)
- PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD (fallbacks; PGPASSWORD defaults to ENV_SECRET)
- ENV_SECRET (optional password fallback, e.g., Supabase service role)
- PGSSLMODE (default: prefer)
- AWS_REGION (optional): AWS region hint for boto3
"""
import io
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from threading import Lock
from typing import Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse
import re

import boto3
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

LOGGER = logging.getLogger(__name__)


def _get_env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass
class LoadConfig:
    bucket: str
    prefix: str
    symbol: str = "BTCUSDT"
    start: datetime = datetime(2025, 12, 11, tzinfo=timezone.utc)
    end: datetime = datetime.now(timezone.utc)
    overlap_seconds: int = 180  # rewind when resuming
    max_keys: Optional[int] = None  # for testing
    max_workers: int = 8  # parallel S3 fetch


def get_s3_client():
    region = _get_env("AWS_REGION")
    return boto3.client("s3", region_name=region)


def get_pg_conn():
    dsn = (
        _get_env("PG_DSN")
        or _get_env("POSTGRES_URL")
        or _get_env("DATABASE_URL")
    )
    sslmode = _get_env("PGSSLMODE", "prefer")

    if dsn:
        parsed = urlparse(dsn)
        if parsed.scheme in {"http", "https"}:
            raise RuntimeError(
                "POSTGRES_URL/DATABASE_URL must be a Postgres DSN "
                "(e.g., postgresql://user:pass@host:5432/dbname), not an HTTPS URL."
            )
        return psycopg2.connect(dsn, sslmode=sslmode)

    password = _get_env("PGPASSWORD") or _get_env("ENV_SECRET")
    if not password:
        raise RuntimeError("Set PGPASSWORD (or ENV_SECRET) for database authentication.")

    return psycopg2.connect(
        host=_get_env("PGHOST", required=True),
        port=int(_get_env("PGPORT", "5432")),
        dbname=_get_env("PGDATABASE", required=True),
        user=_get_env("PGUSER", "postgres"),
        password=password,
        sslmode=sslmode,
    )


def list_parquet_keys(client, bucket: str, prefix: str) -> Iterable[str]:
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".parquet"):
                yield key


def key_to_datetime(key: str) -> Optional[datetime]:
    # Expected: .../<YYYY>/<MM>/<DD>/<HH>/<MM>/filename.parquet
    m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/(\d{2})/(\d{2})/", key)
    if not m:
        return None
    year, month, day, hour, minute = map(int, m.groups())
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def fetch_parquet(client, bucket: str, key: str) -> pd.DataFrame:
    obj = client.get_object(Bucket=bucket, Key=key)
    body = io.BytesIO(obj["Body"].read())
    return pd.read_parquet(body)


def dedup_trades(df: pd.DataFrame, seen_trade_ids: Set[int]) -> pd.DataFrame:
    if "trade_id" not in df.columns:
        return df
    trade_ids = df["trade_id"].dropna().astype(int)
    mask = ~trade_ids.isin(seen_trade_ids)
    deduped = df.loc[mask].copy()
    seen_trade_ids.update(deduped["trade_id"].dropna().astype(int))
    return deduped


def normalize_and_filter(
    df: pd.DataFrame, symbol: str, start: datetime, end: datetime
) -> pd.DataFrame:
    if df.empty:
        return df
    if "event_time" not in df.columns:
        raise ValueError("Missing required column 'event_time'")
    df = df[df["symbol"] == symbol]
    if df.empty:
        return df
    df["event_time"] = pd.to_datetime(df["event_time"], utc=True, errors="coerce", format="mixed")
    df["trade_time"] = pd.to_datetime(df["trade_time"], utc=True, errors="coerce", format="mixed")
    df = df.dropna(subset=["event_time"])
    df = df[(df["event_time"] >= start) & (df["event_time"] <= end)]
    if df.empty:
        return df
    df = df.sort_values("event_time")
    return df


BUCKET = "15s"


def compute_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df["ts"] = df["event_time"].dt.floor(BUCKET)
    grouped = (
        df.groupby("ts")
        .agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("quantity", "sum"),
        )
        .reset_index()
        .sort_values("ts")
    )
    grouped["price"] = grouped["close"]
    return grouped


def fetch_watermark(conn, fallback: datetime, overlap_seconds: int) -> datetime:
    with conn.cursor() as cur:
        cur.execute("SELECT max(ts) FROM price_1s")
        row = cur.fetchone()
    if row and row[0]:
        wm = row[0] - timedelta(seconds=overlap_seconds)
        return wm if wm > fallback else fallback
    return fallback


def upsert_price_1s(conn, rows: List[Tuple[datetime, float, float, float, float, float, float]]):
    if not rows:
        return 0
    sql = """
    INSERT INTO price_1s (ts, price, open, high, low, close, volume)
    VALUES %s
    ON CONFLICT (ts) DO UPDATE
    SET price = EXCLUDED.price,
        open = EXCLUDED.open,
        high = EXCLUDED.high,
        low = EXCLUDED.low,
        close = EXCLUDED.close,
        volume = EXCLUDED.volume;
    """
    with conn.cursor() as cur:
        execute_values(cur, sql, rows)
    conn.commit()
    return len(rows)


def collect_ohlcv(
    client,
    cfg: LoadConfig,
    seen_trade_ids: Optional[Set[int]] = None,
) -> pd.DataFrame:
    seen_trade_ids = seen_trade_ids or set()
    lock = Lock()
    all_frames: List[pd.DataFrame] = []

    # Pre-filter keys by datetime range
    candidate_keys = []
    for key in list_parquet_keys(client, cfg.bucket, cfg.prefix):
        key_dt = key_to_datetime(key)
        if key_dt is None:
            continue
        if key_dt < cfg.start or key_dt > cfg.end:
            continue
        candidate_keys.append(key)
        if cfg.max_keys and len(candidate_keys) >= cfg.max_keys:
            break

    if not candidate_keys:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "price"])

    max_workers = max(1, cfg.max_workers or 1)

    def process_key(key: str) -> Optional[pd.DataFrame]:
        try:
            LOGGER.info("Processing %s", key)
            df = fetch_parquet(client, cfg.bucket, key)
            if df.empty:
                return None
            df = normalize_and_filter(df, cfg.symbol, cfg.start, cfg.end)
            if df.empty:
                return None
            if "trade_id" in df.columns:
                trade_ids = df["trade_id"].dropna().astype(int)
                if trade_ids.empty:
                    return None
                with lock:
                    new_ids = [tid for tid in trade_ids if tid not in seen_trade_ids]
                    seen_trade_ids.update(new_ids)
                if not new_ids:
                    return None
                df = df[df["trade_id"].isin(new_ids)]
                if df.empty:
                    return None
            ohlcv = compute_ohlcv(df)
            return ohlcv if not ohlcv.empty else None
        except Exception as exc:
            LOGGER.warning("Failed processing %s: %s", key, exc)
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_key, key): key for key in candidate_keys}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                all_frames.append(result)

    if not all_frames:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "price"])

    combined = pd.concat(all_frames).sort_values("ts")
    merged = (
        combined.groupby("ts")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .reset_index()
        .sort_values("ts")
    )
    merged["price"] = merged["close"]
    return merged
