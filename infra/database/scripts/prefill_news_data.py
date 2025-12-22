#!/usr/bin/env python3
"""
Prefill news data from S3 into a local CSV using the same logic as the news_data_ingestor Lambda.
- Reads JSON objects under ExtContent/news_data/YYYY/MM/DD/*.json
- Filters by crawlDate >= start (default 2025-12-11T00:00:00Z) and <= end (default now)
- Fetches article HTML and extracts text; falls back to DESC/description if fetch fails
- Writes/merges to CSV, deduping by link (or published_at+title)
"""
import argparse
import json
import logging
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import boto3
import pandas as pd
import requests

LOGGER = logging.getLogger(__name__)

DEFAULT_START = os.getenv("PREFILL_NEWS_START", "2025-12-11T00:00:00Z")
DEFAULT_MAX_BYTES = int(os.getenv("NEWS_MAX_ARTICLE_BYTES", "524288"))
DEFAULT_MAX_CHARS = int(os.getenv("NEWS_MAX_ARTICLE_CHARS", "8000"))


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def key_date(key: str) -> Optional[datetime]:
    m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", key)
    if not m:
        return None
    y, mth, d = map(int, m.groups())
    return datetime(y, mth, d, tzinfo=timezone.utc)


def extract_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if "data" in payload and isinstance(payload["data"], list):
            return [item for item in payload["data"] if isinstance(item, dict)]
        return [payload]
    return []


def first_nonempty(item: Dict[str, Any], keys: Iterable[str]) -> Optional[str]:
    for key in keys:
        val = item.get(key)
        if val:
            return str(val)
    return None


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._texts: List[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs):  # noqa: ANN001
        if tag in {"script", "style"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if text:
            self._texts.append(text)

    def get_text(self) -> str:
        return " ".join(self._texts)


def fetch_article_content(url: str, max_bytes: int, max_chars: int) -> str:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "news-prefill/1.0"},
            timeout=(3, 10),
            stream=True,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("Request failed for %s: %s", url, exc)
        return ""

    if resp.status_code != 200:
        LOGGER.info("HTTP %s for %s", resp.status_code, url)
        return ""

    try:
        raw = b""
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                raw += chunk
            if len(raw) >= max_bytes:
                break
        html = raw.decode(resp.encoding or "utf-8", errors="ignore")
    finally:
        resp.close()

    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("HTML parse failed for %s: %s", url, exc)
        return ""

    text = parser.get_text().strip()
    if len(text) > max_chars:
        return text[:max_chars]
    return text


def build_rows(
    items: List[Dict[str, Any]],
    start: datetime,
    end: datetime,
    max_bytes: int,
    max_chars: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in items:
        crawl_raw = first_nonempty(item, ["crawlDate", "crawl_date", "CrawlDate", "PUB_DTTM"])
        crawl_dt = parse_dt(crawl_raw) if crawl_raw else None
        if crawl_dt is None or crawl_dt < start or crawl_dt > end:
            continue

        url = first_nonempty(item, ["URL", "url"])
        content = fetch_article_content(url, max_bytes, max_chars) if url else ""
        if not content:
            content = first_nonempty(item, ["DESC", "description"]) or ""
        if not content:
            continue

        title = first_nonempty(item, ["TITLE", "title"])
        summary = first_nonempty(item, ["DESC", "description"])
        rows.append(
            {
                "published_at": crawl_dt.isoformat(),
                "title": title,
                "summary": summary,
                "link": url,
                "content": content,
            }
        )
    return rows


def dedup_and_sort(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True, errors="coerce")
    df = df.dropna(subset=["published_at"])
    df["link"] = df["link"].fillna("")
    if df["link"].str.len().any():
        df = df.drop_duplicates(subset=["link"], keep="first")
    else:
        df = df.drop_duplicates(subset=["published_at", "title"], keep="first")
    df = df.sort_values("published_at")
    df["published_at"] = df["published_at"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return df


def write_csv_merge(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        prev = pd.read_csv(path)
        merged = pd.concat([prev, df], ignore_index=True)
    else:
        merged = df
    merged = dedup_and_sort(merged.to_dict(orient="records"))
    merged.to_csv(path, index=False)
    LOGGER.info("Wrote %s rows to %s", len(merged), path)


def collect_keys(client, bucket: str, prefix: str, start: datetime, end: datetime, max_keys: Optional[int]):
    keys: List[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            k_dt = key_date(key)
            if k_dt and (k_dt < start or k_dt > end):
                continue
            keys.append(key)
            if max_keys and len(keys) >= max_keys:
                return keys
    return keys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True, help="S3 bucket containing news_data")
    parser.add_argument("--prefix", default="ExtContent/news_data/", help="S3 prefix (default: ExtContent/news_data/)")
    parser.add_argument("--start", default=DEFAULT_START, help="ISO timestamp (default: 2025-12-11T00:00:00Z)")
    parser.add_argument("--end", default=None, help="ISO timestamp (default: now UTC)")
    parser.add_argument("--dump-csv", default="./news_prefill.csv", help="Path to write merged CSV")
    parser.add_argument("--max-keys", type=int, default=None, help="Optional limit for debugging")
    parser.add_argument("--max-article-bytes", type=int, default=DEFAULT_MAX_BYTES, help="Limit HTML bytes per article")
    parser.add_argument("--max-article-chars", type=int, default=DEFAULT_MAX_CHARS, help="Limit extracted characters")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    start = parse_dt(args.start)
    end = parse_dt(args.end) if args.end else datetime.now(timezone.utc)

    s3 = boto3.client("s3")
    keys = collect_keys(s3, args.bucket, args.prefix, start, end, args.max_keys)
    if not keys:
        LOGGER.info("No keys found in range.")
        return

    all_rows: List[Dict[str, Any]] = []
    for key in keys:
        try:
            obj = s3.get_object(Bucket=args.bucket, Key=key)
            payload = json.loads(obj["Body"].read())
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to read %s: %s", key, exc)
            continue
        items = extract_items(payload)
        rows = build_rows(items, start, end, args.max_article_bytes, args.max_article_chars)
        if rows:
            all_rows.extend(rows)
        LOGGER.info("Processed %s rows from %s (total so far %s)", len(rows), key, len(all_rows))

    if not all_rows:
        LOGGER.info("No rows collected; nothing to write.")
        return

    df = dedup_and_sort(all_rows)
    write_csv_merge(df, Path(args.dump_csv).expanduser().resolve())


if __name__ == "__main__":
    main()
