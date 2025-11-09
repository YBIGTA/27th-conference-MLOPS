import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

import boto3


s3 = boto3.client("s3")
BUCKET = os.environ["BUCKET_NAME"]
DATA_SOURCE = os.environ.get("NEWS_DATA_SOURCE", "TEST")


def _build_key(timestamp: datetime) -> str:
    return timestamp.strftime(f"Ext/{DATA_SOURCE}/%Y/%m/%d/news-%H%M%S.json")


def handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    key = _build_key(now)
    payload = {
        "title": f"{now.isoformat()}-News",
        "body": f"{now.isoformat()}-News",
        "source": DATA_SOURCE,
        "ingested_at": now.isoformat(),
        "event": event,
    }
    s3.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json",
    )
    return {"status": "ok", "bucket": BUCKET, "key": key}
