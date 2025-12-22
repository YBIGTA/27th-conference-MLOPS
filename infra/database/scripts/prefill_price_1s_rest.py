#!/usr/bin/env python3
"""
Prefill via Supabase REST (fallback when direct Postgres is blocked).
Reads Parquet ticks from S3, builds 1s OHLCV, and upserts into price_1s using REST.

Environment:
  AWS_REGION=ap-northeast-2
  SUPABASE_URL=https://kmeqefefwoyjxzfflhye.supabase.co
  SUPABASE_SERVICE_ROLE_KEY=<service-role-key>  # ENV_SECRET와 동일
  PREFILL_START=2025-12-11T00:00:00Z
  PREFILL_END=2025-12-11T13:30:00+09:00

Usage:
  python -m infra.database.scripts.prefill_price_1s_rest \
    --bucket ybigta-mlops-landing-zone-324037321745 \
    --prefix Binance/BTCUSDT/
"""
import argparse
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Dict, Any
from pathlib import Path

import pandas as pd
import requests

try:
    from .price_1s_utils import (
        LoadConfig,
        collect_ohlcv,
    )
except ImportError:
    from price_1s_utils import (
        LoadConfig,
        collect_ohlcv,
    )


LOGGER = logging.getLogger(__name__)


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def chunked(items: List[Dict[str, Any]], size: int = 500):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def write_csv_accumulate(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        prev = pd.read_csv(path, parse_dates=["ts"])
        combined = pd.concat([prev, df], ignore_index=True)
        combined["ts"] = pd.to_datetime(combined["ts"], utc=True, errors="coerce")
        combined = combined.dropna(subset=["ts"])
    else:
        combined = df.copy()
    combined = combined.sort_values("ts").drop_duplicates(subset=["ts"], keep="last")
    combined["ts"] = combined["ts"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    combined.to_csv(path, index=False)
    LOGGER.info("Wrote %s rows (dedup by ts) to %s", len(combined), path)


def upsert_rest(rows: List[Dict[str, Any]]) -> int:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("ENV_SECRET")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or ENV_SECRET) are required.")
    endpoint = f"{url}/rest/v1/price_1s"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    inserted = 0
    for chunk in chunked(rows, 500):
        resp = requests.post(endpoint, headers=headers, data=json.dumps(chunk))
        if not resp.ok:
            raise RuntimeError(f"REST upsert failed: {resp.status_code} {resp.text}")
        inserted += len(chunk)
    return inserted


def main() -> None:
    default_start = os.getenv("PREFILL_START", "2025-12-11T00:00:00Z")
    default_end = os.getenv("PREFILL_END")  # e.g., 2025-12-11T13:30:00+09:00

    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="Binance/BTCUSDT/")
    parser.add_argument("--start", default=default_start)
    parser.add_argument("--end", default=default_end, help="ISO timestamp (default: now UTC)")
    parser.add_argument("--max-keys", type=int, default=None, help="debug: limit number of parquet objects")
    parser.add_argument("--dump-csv", default=None, help="Optional path to write aggregated OHLCV as CSV")
    parser.add_argument("--dump-json", default=None, help="Optional path to write aggregated OHLCV as JSON lines")
    parser.add_argument("--skip-upload", action="store_true", help="Skip REST upsert (only dump locally)")
    parser.add_argument(
        "--daily-dump-dir",
        default=None,
        help="Optional directory to write per-day CSVs (price_1s_YYYY-MM-DD.csv)",
    )
    parser.add_argument(
        "--chunk-hours",
        type=int,
        default=0,
        help="If >0, write per-chunk CSVs of this many hours (UTC) into chunk-dir.",
    )
    parser.add_argument(
        "--chunk-dir",
        default="./price_1s_chunks",
        help="Directory to write chunked CSVs when --chunk-hours is set.",
    )
    parser.add_argument(
        "--flush-every-hours",
        type=int,
        default=0,
        help="If >0, process in rolling windows of this size and append to dump-csv (ts-dedup).",
    )
    args = parser.parse_args()

    # If skip-upload and no dump path provided, default to current directory
    if args.skip_upload and not args.dump_csv and not args.dump_json:
        args.dump_csv = str(Path.cwd() / "price_1s.csv")

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

    import boto3

    s3 = boto3.client("s3")

    seen_trade_ids = set()

    def process_window(win_start: datetime, win_end: datetime) -> pd.DataFrame:
        cfg.start = win_start
        cfg.end = win_end
        LOGGER.info("Collecting OHLCV from %s to %s (bucket=%s prefix=%s)", cfg.start, cfg.end, cfg.bucket, cfg.prefix)
        return collect_ohlcv(s3, cfg, seen_trade_ids=seen_trade_ids)

    df: pd.DataFrame
    if args.flush_every_hours and args.flush_every_hours > 0:
        if not args.dump_csv:
            args.dump_csv = str(Path.cwd() / "price_1s.csv")
        cursor = start
        while cursor < end:
            win_end = min(cursor + pd.Timedelta(hours=args.flush_every_hours), end)
            chunk_df = process_window(cursor, win_end)
            if chunk_df.empty:
                LOGGER.info("No data in window %s to %s", cursor, win_end)
            else:
                csv_path = Path(args.dump_csv).expanduser().resolve()
                write_csv_accumulate(chunk_df.copy(), csv_path)
            cursor = win_end
        df = pd.DataFrame()  # already flushed
        if args.skip_upload:
            LOGGER.info("skip-upload requested; exiting without REST upsert.")
            return
    else:
        df = process_window(start, end)
        if df.empty:
            LOGGER.info("No data to upsert.")
            return

    # Optional local dump
    if args.dump_csv:
        csv_path = Path(args.dump_csv).expanduser().resolve()
        dump_df = df.copy()
        write_csv_accumulate(dump_df, csv_path)
    if args.dump_json:
        dump_df = df.copy()
        dump_df["ts"] = dump_df["ts"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
        json_path = Path(args.dump_json).expanduser().resolve()
        dump_df.to_json(json_path, orient="records", lines=True, force_ascii=False)
        LOGGER.info("Dumped %s rows to %s (JSONL)", len(dump_df), json_path)
    if args.daily_dump_dir:
        out_dir = Path(args.daily_dump_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        df_tmp = df.copy()
        df_tmp["ts"] = df_tmp["ts"].dt.tz_convert(timezone.utc) if df_tmp["ts"].dt.tz is not None else df_tmp["ts"]
        df_tmp["ts_str"] = df_tmp["ts"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
        for day, group in df_tmp.groupby(df_tmp["ts"].dt.date):
            fname = out_dir / f"price_1s_{day}.csv"
            group_out = group.drop(columns=["ts"]).rename(columns={"ts_str": "ts"})
            group_out.to_csv(fname, index=False)
            LOGGER.info("Dumped %s rows to %s", len(group_out), fname)
    if args.chunk_hours and args.chunk_hours > 0:
        out_dir = Path(args.chunk_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        df_tmp = df.copy()
        df_tmp["ts"] = df_tmp["ts"].dt.tz_convert(timezone.utc) if df_tmp["ts"].dt.tz is not None else df_tmp["ts"]
        df_tmp["chunk_start"] = df_tmp["ts"].dt.floor(f"{args.chunk_hours}h")
        for chunk_start, group in df_tmp.groupby("chunk_start"):
            chunk_end = chunk_start + pd.Timedelta(hours=args.chunk_hours)
            fname = out_dir / f"price_1s_{chunk_start.strftime('%Y%m%dT%H%M%SZ')}_{args.chunk_hours}h.csv"
            group_out = group.drop(columns=["chunk_start"]).copy()
            group_out["ts"] = group_out["ts"].dt.strftime("%Y-%m-%dT%H:%M:%S%z")
            group_out.to_csv(fname, index=False)
            LOGGER.info("Dumped %s rows to %s (%s to %s)", len(group_out), fname, chunk_start, chunk_end)
    if args.skip_upload:
        LOGGER.info("skip-upload requested; exiting without REST upsert.")
        return

    records = []
    for _, row in df.iterrows():
        records.append(
            {
                "ts": row["ts"].isoformat(),
                "price": float(row["price"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    rows = records
    inserted = upsert_rest(rows)
    LOGGER.info("Upserted %s rows into price_1s via REST", inserted)


if __name__ == "__main__":
    main()
