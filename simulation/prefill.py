import argparse
import asyncio
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from openai import AsyncOpenAI
import csv
import json


def load_env_file(path: Path) -> bool:
    """Lightweight .env loader (no extra dependency)."""
    if not path.exists():
        return False
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    return True


def preload_env():
    here_env = Path(__file__).resolve().parent / ".env"
    cwd_env = Path.cwd() / ".env"
    loaded = load_env_file(here_env)
    if cwd_env != here_env:
        loaded = load_env_file(cwd_env) or loaded
    return loaded


preload_env()

# Required env vars (after preload)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Model choices (override via env if needed)
GPT_MODEL = os.getenv("GPT_MODEL", "gpt-5-mini")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")

# Constants
PRICE_TABLE = "price_15s"
NEWS_TABLE = "news"
OUTPUT_TABLE = "ai_outputs"

PRICE_WINDOW_ROWS = 40  # 10 minutes of 15s bars (default requirement)
NEWS_LIMIT = 6
PAGE_SIZE = 5000  # Supabase pagination for long ranges
DEFAULT_CSV_LOG = Path(__file__).resolve().parent / "prefill_log.csv"
DEFAULT_JSON_LOG = Path(__file__).resolve().parent / "prefill_log.jsonl"


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def require_env():
    missing = []
    if not SUPABASE_URL:
        missing.append("SUPABASE_URL")
    if not SUPABASE_SERVICE_ROLE_KEY:
        missing.append("SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_API_KEY)")
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")


def to_utc(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def isoformat(dt: datetime) -> str:
    return to_utc(dt).replace(microsecond=0).isoformat()


def truncate_to_10m(dt: datetime) -> datetime:
    dt = to_utc(dt)
    minute = (dt.minute // 10) * 10
    return dt.replace(minute=minute, second=0, microsecond=0)


def iter_base_times(start: datetime, end: datetime):
    current = truncate_to_10m(start)
    end = truncate_to_10m(end)
    while current <= end:
        yield current
        current += timedelta(minutes=10)


async def fetch_last_output_ts(http_client: httpx.AsyncClient) -> Optional[datetime]:
    """Return most recent base_ts from ai_outputs."""
    url = f"{SUPABASE_URL}/rest/v1/{OUTPUT_TABLE}"
    params = {"select": "base_ts", "order": "base_ts.desc", "limit": "1"}
    resp = await http_client.get(url, params=params, headers=supabase_headers(), timeout=10)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    ts_raw = rows[0].get("base_ts")
    if not ts_raw:
        return None
    return datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(timezone.utc)


async def fetch_first_price_ts(http_client: httpx.AsyncClient) -> Optional[datetime]:
    """Return earliest ts from price table."""
    url = f"{SUPABASE_URL}/rest/v1/{PRICE_TABLE}"
    params = {"select": "ts", "order": "ts.asc", "limit": "1"}
    resp = await http_client.get(url, params=params, headers=supabase_headers(), timeout=10)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    ts_raw = rows[0].get("ts")
    if not ts_raw:
        return None
    return datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(timezone.utc)


def ensure_csv_log(path: Path, fieldnames: List[str]):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()


def append_csv_log(path: Path, fieldnames: List[str], row: Dict[str, Any]):
    ensure_csv_log(path, fieldnames)
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerow({k: row.get(k, "") for k in fieldnames})


def append_json_log(path: Path, row: Dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False))
        f.write("\n")


def supabase_headers() -> Dict[str, str]:
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }


async def fetch_price_window(client: httpx.AsyncClient, base_ts: datetime) -> List[Candle]:
    """Get the 40 rows before base_ts (exclusive), ordered ascending."""
    url = f"{SUPABASE_URL}/rest/v1/{PRICE_TABLE}"
    params = {
        "select": "ts,open,high,low,close,volume",
        "order": "ts.desc",
        "limit": str(PRICE_WINDOW_ROWS),
        "ts": f"lt.{isoformat(base_ts)}",
    }
    resp = await client.get(url, params=params, headers=supabase_headers(), timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    candles = []
    for row in reversed(rows):  # ascending time order
        ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00")).astimezone(timezone.utc)
        candles.append(
            Candle(
                ts=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume") or 0.0),
            )
        )
    return candles


async def fetch_price_range(client: httpx.AsyncClient, start: datetime, end: datetime) -> List[Candle]:
    """Get price rows between [start, end) ordered ascending with pagination."""
    url = f"{SUPABASE_URL}/rest/v1/{PRICE_TABLE}"
    headers = supabase_headers()
    headers["Range-Unit"] = "items"
    items: List[Candle] = []
    offset = 0
    start_iso = isoformat(start)
    end_iso = isoformat(end)
    while True:
        headers["Range"] = f"{offset}-{offset + PAGE_SIZE - 1}"
        params = {
            "select": "ts,open,high,low,close,volume",
            "order": "ts.asc",
            "and": f"(ts.gte.{start_iso},ts.lt.{end_iso})",
        }
        resp = await client.get(url, params=params, headers=headers, timeout=30)
        if resp.status_code not in (200, 206):
            resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        for row in rows:
            ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00")).astimezone(timezone.utc)
            items.append(
                Candle(
                    ts=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                )
            )
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return items


async def fetch_news(client: httpx.AsyncClient, base_ts: datetime) -> List[Dict[str, Any]]:
    url = f"{SUPABASE_URL}/rest/v1/{NEWS_TABLE}"
    params = {
        "select": "id,published_at,title,summary,link",
        "order": "published_at.desc",
        "limit": str(NEWS_LIMIT),
        "published_at": f"lt.{isoformat(base_ts)}",
    }
    resp = await client.get(url, params=params, headers=supabase_headers(), timeout=10)
    resp.raise_for_status()
    rows = resp.json()
    items = []
    for row in rows:
        ts = datetime.fromisoformat(row["published_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
        items.append(
            {
                "id": row.get("id"),
                "published_at": isoformat(ts),
                "title": row.get("title"),
                "summary": row.get("summary"),
                "link": row.get("link"),
            }
        )
    return items


def to_daily_bars(candles: List[Candle]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for c in candles:
        day_key = c.ts.date().isoformat()
        bucket = buckets.get(day_key)
        if not bucket:
            buckets[day_key] = {
                "date": day_key,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
        else:
            bucket["high"] = max(bucket["high"], c.high)
            bucket["low"] = min(bucket["low"], c.low)
            bucket["close"] = c.close
            bucket["volume"] += c.volume
    return [buckets[k] for k in sorted(buckets.keys())]


def fmt_float(value: float) -> str:
    """Compact float formatter for deterministic tables."""
    return f"{value:.4f}"


def build_text_type_a(window: List[Candle]) -> str:
    lines = ["[Short-term Price Trend]", "ts,open,high,low,close,volume"]
    for c in window:
        lines.append(
            f"{isoformat(c.ts)},{fmt_float(c.open)},{fmt_float(c.high)},{fmt_float(c.low)},"
            f"{fmt_float(c.close)},{fmt_float(c.volume)}"
        )
    return "\n".join(lines)


def first_sentences(text: str, max_sentences: int = 3) -> str:
    """Return up to max_sentences sentences from text."""
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    trimmed = [s.strip() for s in sentences if s.strip()]
    return " ".join(trimmed[:max_sentences])


def build_text_type_b(daily_bars: List[Dict[str, Any]], news_items: List[Dict[str, Any]]) -> str:
    price_lines = ["[10-day Price Trend]", "date,open,high,low,close,volume"]
    for bar in daily_bars:
        price_lines.append(
            f"{bar['date']},{fmt_float(bar['open'])},{fmt_float(bar['high'])},"
            f"{fmt_float(bar['low'])},{fmt_float(bar['close'])},{fmt_float(bar['volume'])}"
        )

    news_lines = ["[Recent Bitcoin News]"]
    for news in news_items[:NEWS_LIMIT]:
        title = news.get("title") or ""
        summary_snippet = first_sentences(news.get("summary") or "", 3)
        if summary_snippet:
            news_lines.append(f"- {title} | {summary_snippet}")
        else:
            news_lines.append(f"- {title}")
    if len(news_lines) == 1:
        news_lines.append("- none")

    return "\n".join(price_lines + [""] + news_lines)


async def embed_text(client: AsyncOpenAI, text: str) -> List[float]:
    resp = await client.embeddings.create(model=EMBED_MODEL, input=text)
    return resp.data[0].embedding


def truncate_and_normalize(vec: List[float], size: int = 256, target_dim: Optional[int] = None) -> List[float]:
    """Take the first `size` dims, L2-normalize, then pad zeros to `target_dim` (default: size)."""
    trimmed = vec[:size]
    norm = math.sqrt(sum(x * x for x in trimmed)) or 1.0
    normalized = [x / norm for x in trimmed]
    target = target_dim or size
    if target > len(normalized):
        normalized.extend([0.0] * (target - len(normalized)))
    return normalized


async def upsert_output(
    client: httpx.AsyncClient,
    base_ts: datetime,
    summary_a: str,
    summary_b: str,
    embedding_a: List[float],
    embedding_b: List[float],
):
    url = f"{SUPABASE_URL}/rest/v1/{OUTPUT_TABLE}?on_conflict=base_ts"
    headers = supabase_headers()
    headers["Prefer"] = "resolution=merge-duplicates,return=representation"
    payload = [
        {
            "base_ts": isoformat(base_ts),
            "text_type_a": summary_a,
            "text_type_b": summary_b,
            "embedding_a": embedding_a,
            "embedding_b": embedding_b,
        }
    ]
    resp = await client.post(url, json=payload, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json()


async def process_base_ts(
    base_ts: datetime,
    ai_client: AsyncOpenAI,
    http_client: httpx.AsyncClient,
    min_price_rows: int = PRICE_WINDOW_ROWS,
) -> Dict[str, Any]:
    window_end = truncate_to_10m(base_ts)
    window_start = window_end - timedelta(minutes=10)

    price_window_raw = await fetch_price_window(http_client, window_end)
    price_window = [c for c in price_window_raw if c.ts >= window_start]
    if len(price_window) < min_price_rows:
        raw_min = price_window_raw[0].ts if price_window_raw else None
        raw_max = price_window_raw[-1].ts if price_window_raw else None
        if not price_window:
            print(
                f"[skip] {isoformat(window_end)} no price rows "
                f"(raw_count={len(price_window_raw)}, raw_range=[{isoformat(raw_min) if raw_min else 'n/a'} .. {isoformat(raw_max) if raw_max else 'n/a'}], "
                f"window=[{isoformat(window_start)} .. {isoformat(window_end)}))"
            )
            return {
                "base_ts": isoformat(window_end),
                "status": "skip",
                "reason": "no_price_rows",
                "price_rows": len(price_window),
                "min_price_rows": min_price_rows,
                "raw_count": len(price_window_raw),
                "raw_range_start": isoformat(raw_min) if raw_min else "",
                "raw_range_end": isoformat(raw_max) if raw_max else "",
                "summary_a": "",
                "summary_b": "",
                "embedding_a": [],
                "embedding_b": [],
            }
        else:
            print(
                f"[warn] {isoformat(window_end)} proceeding with partial price rows "
                f"({len(price_window)}/{min_price_rows} required). raw_count={len(price_window_raw)}, "
                f"raw_range=[{isoformat(raw_min) if raw_min else 'n/a'} .. {isoformat(raw_max) if raw_max else 'n/a'}]"
            )

    ten_day_start = window_end - timedelta(days=10)
    price_range = await fetch_price_range(http_client, ten_day_start, window_end)
    daily_bars = to_daily_bars(price_range)

    news_items = await fetch_news(http_client, window_end)

    text_type_a = build_text_type_a(price_window)
    embedding_a_full = await embed_text(ai_client, text_type_a)
    embedding_a = truncate_and_normalize(embedding_a_full, size=256, target_dim=256)

    text_type_b = build_text_type_b(daily_bars, news_items)
    embedding_b_full = await embed_text(ai_client, text_type_b)
    embedding_b = truncate_and_normalize(embedding_b_full, size=256, target_dim=256)

    await upsert_output(http_client, window_end, text_type_a, text_type_b, embedding_a, embedding_b)
    print(f"[ok] upserted ai_outputs for base_ts={isoformat(window_end)}")
    return {
        "base_ts": isoformat(window_end),
        "status": "ok",
        "reason": "",
        "price_rows": len(price_window),
        "min_price_rows": min_price_rows,
        "raw_count": len(price_window_raw),
        "raw_range_start": isoformat(price_window_raw[0].ts) if price_window_raw else "",
        "raw_range_end": isoformat(price_window_raw[-1].ts) if price_window_raw else "",
        "summary_a": text_type_a,
        "summary_b": text_type_b,
        "embedding_a": embedding_a,
        "embedding_b": embedding_b,
    }


async def main(args):
    require_env()
    csv_path = Path(args.csv_path).expanduser().resolve() if args.csv_path else None
    json_path = Path(args.json_path).expanduser().resolve() if args.json_path else None
    csv_fields = [
        "base_ts",
        "status",
        "reason",
        "price_rows",
        "min_price_rows",
        "raw_count",
        "raw_range_start",
        "raw_range_end",
        "summary_a",
        "summary_b",
    ]

    ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    async with httpx.AsyncClient() as http_client:
        # Determine start/end based on DB gap
        now_ts = truncate_to_10m(datetime.now(timezone.utc))
        end = truncate_to_10m(args.to_ts or now_ts)

        if args.from_ts:
            start = truncate_to_10m(args.from_ts)
        else:
            last_ts = await fetch_last_output_ts(http_client)
            if last_ts:
                start = truncate_to_10m(last_ts + timedelta(minutes=10))
            else:
                first_price_ts = await fetch_first_price_ts(http_client)
                if not first_price_ts:
                    raise RuntimeError("No price data available to infer start time.")
                start = truncate_to_10m(first_price_ts + timedelta(minutes=10))

        if start > end:
            print(f"[info] start {isoformat(start)} is after end {isoformat(end)}, nothing to do.")
            return

        for base_ts in iter_base_times(start, end):
            try:
                row = await process_base_ts(base_ts, ai_client, http_client, min_price_rows=args.min_price_rows)
            except Exception as exc:  # log and continue
                print(f"[error] base_ts={isoformat(base_ts)}: {exc}")
                row = {
                    "base_ts": isoformat(truncate_to_10m(base_ts)),
                    "status": "error",
                    "reason": str(exc),
                    "price_rows": "",
                    "min_price_rows": args.min_price_rows,
                    "raw_count": "",
                    "raw_range_start": "",
                    "raw_range_end": "",
                    "summary_a": "",
                    "summary_b": "",
                    "embedding_a": [],
                    "embedding_b": [],
                }
            if csv_path:
                append_csv_log(csv_path, csv_fields, row)
            if json_path:
                append_json_log(json_path, row)
            if args.sleep_seconds:
                await asyncio.sleep(args.sleep_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prefill ai_outputs via Supabase REST + OpenAI.")
    parser.add_argument("--from-ts", type=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")), help="Base ts (inclusive) start, UTC ISO.")
    parser.add_argument("--to-ts", type=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")), help="Base ts (inclusive) end, UTC ISO.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0, help="Sleep between base_ts iterations.")
    parser.add_argument(
        "--min-price-rows",
        type=int,
        default=PRICE_WINDOW_ROWS,
        help="Minimum 15s rows required for a 10m window; lower to allow partial windows (default 40).",
    )
    parser.add_argument(
        "--csv-path",
        type=str,
        default=str(DEFAULT_CSV_LOG),
        help=f"CSV log path (default: {DEFAULT_CSV_LOG}). Set empty string to disable.",
    )
    parser.add_argument(
        "--json-path",
        type=str,
        default=str(DEFAULT_JSON_LOG),
        help=f"JSONL log path (default: {DEFAULT_JSON_LOG}). Set empty string to disable.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
