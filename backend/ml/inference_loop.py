import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import boto3
import requests
import torch

from local_inference import download_and_extract, load_latest_uri, load_model, parse_embedding, predict_single


def log(msg: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    print(f"[ml-infer {now}] {msg}", flush=True)


def fetch_latest_embedding(supabase_url: str, supabase_key: str, table: str = "ai_outputs") -> Optional[Dict]:
    url = f"{supabase_url}/rest/v1/{table}"
    params = {
        "select": "base_ts,embedding_a,embedding_b",
        "order": "base_ts.desc",
        "limit": "1",
    }
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
    }
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    if resp.status_code != 200:
        log(f"Supabase fetch failed {resp.status_code}: {resp.text[:200]}")
        return None
    rows = resp.json()
    if not rows:
        log("Supabase returned no rows")
        return None
    return rows[0]


def main() -> None:
    interval = int(os.getenv("INFER_INTERVAL_SECONDS", "600"))
    model_s3 = os.getenv("MODEL_JSON", os.getenv("MODEL_S3", "s3://ybigta-mlops-landing-zone-324037321745/model/latest.json"))
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_API_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not supabase_key:
        log("SUPABASE_URL / SUPABASE_API_KEY 환경변수가 필요합니다.")
        sys.exit(1)

    device = torch.device("cpu")
    cached_uri: Optional[str] = None
    model = None
    meta: Dict = {}
    workdir = Path("/tmp/ml_infer")
    workdir.mkdir(parents=True, exist_ok=True)

    while True:
        try:
            target_uri = load_latest_uri(model_s3) if model_s3.endswith(".json") else model_s3
            if target_uri != cached_uri or model is None:
                log(f"loading model from {target_uri}")
                model_dir = download_and_extract(target_uri, workdir)
                model, meta = load_model(model_dir, device)
                cached_uri = target_uri

            row = fetch_latest_embedding(supabase_url, supabase_key)
            if not row:
                time.sleep(interval)
                continue
            a = parse_embedding(row.get("embedding_a"))
            b = parse_embedding(row.get("embedding_b"))
            base_ts = row.get("base_ts")
            if not a or not b:
                log("embedding parse failed; skipping")
                time.sleep(interval)
                continue

            preds = predict_single(model, a, b, device)
            targets = meta.get("target_cols", [])
            pred_map = {targets[i] if i < len(targets) else f"t{i}": preds[i] for i in range(len(preds))}
            log(f"pred base_ts={base_ts} model={cached_uri} preds={pred_map}")
        except Exception as e:
            log(f"error: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
