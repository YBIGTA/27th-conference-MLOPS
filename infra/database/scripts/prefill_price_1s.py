#!/usr/bin/env python3
"""
Local prefill script:
Reads Binance BTCUSDT Parquet ticks from S3, builds 1s OHLCV, and upserts into price_1s.

Example:
  AWS_REGION=ap-northeast-2 \
  POSTGRES_URL=postgresql://user:pass@host:5432/db \
  PREFILL_END=2025-12-11T13:00:00+09:00 \
  # 또는 PGHOST/PGDATABASE/PGUSER/PGPASSWORD/ENV_SECRET 조합 \
  python -m infra.database.scripts.prefill_price_1s \
    --bucket ybigta-mlops-landing-zone-324037321745 \
    --prefix Binance/BTCUSDT/ \
    --start 2025-12-11T00:00:00Z
"""
import argparse
import logging
import os
from datetime import datetime, timezone

import pandas as pd

try:
    # Package import when executed with `python -m infra.database.scripts.prefill_price_1s`
    from .price_1s_utils import (
        LoadConfig,
        collect_ohlcv,
        fetch_watermark,
        get_pg_conn,
        get_s3_client,
        upsert_price_1s,
    )
except ImportError:
    # Fallback for direct script execution
    from price_1s_utils import (
        LoadConfig,
        collect_ohlcv,
        fetch_watermark,
        get_pg_conn,
        get_s3_client,
        upsert_price_1s,
    )


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def main() -> None:
    default_start = os.getenv("PREFILL_START", "2025-12-11T00:00:00Z")
    default_end = os.getenv("PREFILL_END")  # e.g., 2025-12-11T13:00:00+09:00

    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="Binance/BTCUSDT/")
    parser.add_argument("--start", default=default_start)
    parser.add_argument("--end", default=default_end, help="ISO timestamp (default: now UTC)")
    parser.add_argument("--max-keys", type=int, default=None, help="debug: limit number of parquet objects")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    start = parse_dt(args.start)
    end = parse_dt(args.end) if args.end else datetime.now(timezone.utc)

    cfg = LoadConfig(
        bucket=args.bucket,
        prefix=args.prefix,
        start=start,
        end=end,
        max_keys=args.max_keys,
        max_workers=int(os.getenv("PREFILL_MAX_WORKERS", "4")),
    )

    s3 = get_s3_client()
    conn = get_pg_conn()

    watermark = fetch_watermark(conn, cfg.start, cfg.overlap_seconds)
    cfg.start = max(cfg.start, watermark)

    logging.info("Collecting OHLCV from %s to %s (bucket=%s prefix=%s)", cfg.start, cfg.end, cfg.bucket, cfg.prefix)
    df: pd.DataFrame = collect_ohlcv(s3, cfg)
    if df.empty:
        logging.info("No data to upsert.")
        return
    rows = list(df[["ts", "price", "open", "high", "low", "close", "volume"]].itertuples(index=False, name=None))
    inserted = upsert_price_1s(conn, rows)
    logging.info("Upserted %s rows into price_1s", inserted)


if __name__ == "__main__":
    main()
