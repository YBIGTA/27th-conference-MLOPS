"""
Lambda handler to ingest news JSON objects from S3 into Supabase.

Triggered by S3 put events for keys under NEWS_DATA_PREFIX (default:
ExtContent/news_data/). Each object is expected to be either a single JSON
document or a list. For dict payloads, a nested "data" list is also supported.

Behavior:
- published_at is taken from the item's crawlDate (UTC).
- content is fetched by requesting the item's URL and extracting visible text.
- rows are inserted into the Supabase table defined by NEWS_TABLE (default: news),
  including optional title/summary/link fields when present.

Environment variables:
- NEWS_DATA_PREFIX (default: ExtContent/news_data/)
- NEWS_TABLE (default: news)
- NEWS_MIN_CRAWL_DATE (default: 2025-12-11T00:00:00Z; skip older rows)
- NEWS_SUPABASE_URL or SUPABASE_URL (required)
- NEWS_SUPABASE_SERVICE_ROLE_KEY or SUPABASE_SERVICE_ROLE_KEY or ENV_SECRET (required)
- NEWS_MAX_ARTICLE_BYTES (default: 524288)
- NEWS_MAX_ARTICLE_CHARS (default: 8000)
- AWS_REGION (optional, boto3 hint)
"""
import json
import logging
import os
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote_plus

import boto3
import urllib3

LOGGER = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION"))
http = urllib3.PoolManager()

NEWS_DATA_PREFIX = os.getenv("NEWS_DATA_PREFIX", "ExtContent/news_data/")
NEWS_TABLE = os.getenv("NEWS_TABLE", "news")
MAX_ARTICLE_BYTES = int(os.getenv("NEWS_MAX_ARTICLE_BYTES", "524288"))
MAX_ARTICLE_CHARS = int(os.getenv("NEWS_MAX_ARTICLE_CHARS", "8000"))


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    min_crawl_date = _parse_datetime(os.getenv("NEWS_MIN_CRAWL_DATE", "2025-12-11T00:00:00Z")) or datetime(
        2025, 12, 11, tzinfo=timezone.utc
    )
    processed_files = 0
    attempted_rows = 0
    inserted_rows = 0

    for record in event.get("Records", []):
        bucket, key = _extract_bucket_key(record)
        if not bucket or not key:
            continue
        if not key.startswith(NEWS_DATA_PREFIX):
            LOGGER.info("Skipping key outside prefix: %s", key)
            continue

        payload = _read_json(bucket, key)
        if payload is None:
            continue

        items = _extract_items(payload)
        rows = _build_rows(items, min_crawl_date)
        if not rows:
            LOGGER.info("No valid rows found in %s", key)
            continue

        inserted = _upsert_supabase(rows, table=NEWS_TABLE)
        processed_files += 1
        attempted_rows += len(rows)
        inserted_rows += inserted
        LOGGER.info("Inserted %s/%s rows from %s", inserted, len(rows), key)

    return {
        "processed_files": processed_files,
        "attempted_rows": attempted_rows,
        "inserted_rows": inserted_rows,
        "table": NEWS_TABLE,
    }


def _extract_bucket_key(record: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    bucket = record.get("s3", {}).get("bucket", {}).get("name")
    key = unquote_plus(record.get("s3", {}).get("object", {}).get("key", ""))
    return bucket, key


def _read_json(bucket: str, key: str) -> Optional[Any]:
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        return json.loads(body)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Failed to read %s/%s: %s", bucket, key, exc)
        return None


def _extract_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        if "data" in payload and isinstance(payload["data"], list):
            return [item for item in payload["data"] if isinstance(item, dict)]
        return [payload]
    return []


def _build_rows(items: List[Dict[str, Any]], min_crawl_date: datetime) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in items:
        crawl_raw = _first_nonempty(item, ["crawlDate", "crawl_date", "CrawlDate", "PUB_DTTM"])
        crawl_dt = _parse_datetime(crawl_raw)
        if crawl_dt is None:
            LOGGER.info("Skipping item without crawlDate: %s", item)
            continue
        if crawl_dt < min_crawl_date:
            continue

        url = _first_nonempty(item, ["URL", "url"])
        content = _fetch_article_content(url) if url else ""
        if not content:
            content = _first_nonempty(item, ["DESC", "description"]) or ""
        if not content:
            LOGGER.info("Skipping item with empty content: %s", item)
            continue

        title = _first_nonempty(item, ["TITLE", "title"])
        summary = _first_nonempty(item, ["DESC", "description"])
        rows.append(
            {
                "published_at": crawl_dt.isoformat(),
                "content": content,
                "title": title,
                "summary": summary,
                "link": url,
            }
        )
    return rows


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _first_nonempty(item: Dict[str, Any], keys: Iterable[str]) -> Optional[str]:
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

    def handle_starttag(self, tag: str, attrs: List[Any]) -> None:  # noqa: ARG002
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


def _fetch_article_content(url: str) -> str:
    try:
        resp = http.request(
            "GET",
            url,
            preload_content=False,
            headers={"User-Agent": "news-data-ingestor/1.0"},
            timeout=urllib3.Timeout(connect=3.0, read=10.0),
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("Request failed for %s: %s", url, exc)
        return ""

    try:
        if resp.status != 200:
            LOGGER.info("HTTP %s for %s", resp.status, url)
            return ""
        raw = resp.read(MAX_ARTICLE_BYTES)
        html = raw.decode("utf-8", errors="ignore")
    finally:
        resp.release_conn()

    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
    except Exception as exc:  # noqa: BLE001
        LOGGER.info("HTML parse failed for %s: %s", url, exc)
        return ""

    text = parser.get_text().strip()
    if len(text) > MAX_ARTICLE_CHARS:
        return text[:MAX_ARTICLE_CHARS]
    return text


def _chunked(items: List[Dict[str, Any]], size: int = 500) -> Iterable[List[Dict[str, Any]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _get_env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _upsert_supabase(rows: List[Dict[str, Any]], table: Optional[str] = None) -> int:
    if not rows:
        return 0
    base_url = _get_env("NEWS_SUPABASE_URL", _get_env("SUPABASE_URL", required=True)).rstrip("/")
    key = os.getenv("NEWS_SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("ENV_SECRET")
    if not key:
        raise RuntimeError("Missing required environment variable: NEWS_SUPABASE_SERVICE_ROLE_KEY or SUPABASE_SERVICE_ROLE_KEY or ENV_SECRET")
    table = table or "news"
    endpoint = f"{base_url}/rest/v1/{table}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    inserted = 0
    for chunk in _chunked(rows, 500):
        resp = http.request(
            "POST",
            endpoint,
            body=json.dumps(chunk),
            headers=headers,
            timeout=urllib3.Timeout(connect=3.0, read=10.0),
        )
        if resp.status >= 300:
            raise RuntimeError(f"Supabase upsert failed ({resp.status}): {resp.data}")
        inserted += len(chunk)
    return inserted
