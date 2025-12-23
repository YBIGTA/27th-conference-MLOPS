import argparse
import io
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import boto3
import numpy as np
import pandas as pd
import requests


EMBED_DIM = 256


def log(msg: str) -> None:
    print(f"[data_prep] {msg}")


def supabase_headers(api_key: str) -> Dict[str, str]:
    return {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def parse_embedding(raw: Any) -> Optional[List[float]]:
    """Coerce embedding from list or Postgres/REST string to float list."""
    if raw is None:
        return None
    if isinstance(raw, list):
        try:
            return [float(x) for x in raw]
        except Exception:
            return None
    if isinstance(raw, str):
        s = raw.strip()
        # Supabase can return "{0.1,0.2}" or "[0.1, 0.2]"
        if s.startswith("{") or s.startswith("["):
            s = s.strip("{}[]")
        if not s:
            return None
        try:
            return [float(x) for x in s.split(",") if x.strip() != ""]
        except Exception:
            return None
    return None


def fetch_table(
    base_url: str,
    api_key: str,
    table: str,
    select_cols: str,
    order_col: str,
    start_ts: Optional[str] = None,
    limit: int = 0,
) -> List[Dict[str, Any]]:
    url = f"{base_url}/rest/v1/{table}"
    page_size = 1000
    start = 0
    rows: List[Dict[str, Any]] = []
    while True:
        headers = supabase_headers(api_key)
        headers["Range"] = f"{start}-{start + page_size - 1}"
        params: Dict[str, str] = {"select": select_cols, "order": f"{order_col}.asc"}
        if start_ts:
            params[order_col] = f"gte.{start_ts}"
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"Supabase {table} fetch failed {resp.status_code}: {resp.text[:200]}")
        batch = resp.json()
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected payload for {table}: {batch}")
        rows.extend(batch)
        if limit and len(rows) >= limit:
            return rows[:limit]
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def normalize_times(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_datetime(df[col], utc=True, errors="coerce")


def build_dataset(ai_rows: List[Dict[str, Any]], sim_rows: List[Dict[str, Any]]) -> pd.DataFrame:
    ai_df = pd.DataFrame(ai_rows)
    sim_df = pd.DataFrame(sim_rows)
    if ai_df.empty or sim_df.empty:
        return pd.DataFrame()

    ai_df["base_ts"] = normalize_times(ai_df, "base_ts")
    sim_df["ts"] = normalize_times(sim_df, "ts")
    sim_df = sim_df.dropna(subset=["ts"])
    ai_df = ai_df.dropna(subset=["base_ts"])

    # Align: embeddings must precede returns by 10 minutes
    sim_df["base_ts"] = sim_df["ts"] - timedelta(minutes=10)

    merged = sim_df.merge(
        ai_df[["base_ts", "embedding_a", "embedding_b"]],
        how="inner",
        on="base_ts",
    )
    if merged.empty:
        return pd.DataFrame()

    feature_list: List[List[float]] = []
    mask_keep: List[bool] = []
    for _, row in merged.iterrows():
        a = parse_embedding(row["embedding_a"])
        b = parse_embedding(row["embedding_b"])
        if not a or not b or len(a) != EMBED_DIM or len(b) != EMBED_DIM:
            mask_keep.append(False)
            feature_list.append([])
            continue
        feat = np.asarray(a + b, dtype=np.float32)
        if feat.shape[0] != EMBED_DIM * 2:
            mask_keep.append(False)
            feature_list.append([])
            continue
        feature_list.append(feat.tolist())
        mask_keep.append(True)

    merged["features"] = feature_list
    merged["keep"] = mask_keep
    merged = merged[merged["keep"]]
    merged = merged.drop(columns=["keep", "embedding_a", "embedding_b"])
    merged = merged.sort_values("ts")
    return merged


def save_parquet_to_s3(df: pd.DataFrame, bucket: str, prefix: str, run_ts: str) -> str:
    key = f"{prefix.rstrip('/')}/run_ts={run_ts}/train.parquet"
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    buf.seek(0)
    s3 = boto3.client("s3")
    s3.upload_fileobj(buf, bucket, key)
    return f"s3://{bucket}/{key}"


def save_latest_metadata(bucket: str, train_uri: str, row_count: int, run_ts: str, prefix: str) -> str:
    meta = {
        "run_ts": run_ts,
        "train_uri": train_uri,
        "row_count": row_count,
        "feature_dim": EMBED_DIM * 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    body = json.dumps(meta, ensure_ascii=False, indent=2)
    key = f"{prefix.rstrip('/')}/latest.json"
    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=body.encode("utf-8"))
    return f"s3://{bucket}/{key}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Supabase → S3 학습 데이터 적재기")
    parser.add_argument("--bucket", default=os.getenv("LANDING_BUCKET", "ybigta-mlops-landing-zone-324037321745"))
    parser.add_argument("--train-prefix", default=os.getenv("TRAIN_PREFIX", "train"))
    parser.add_argument("--since-ts", default=os.getenv("SINCE_TS"), help="ISO 시점 이후만 가져오기 (옵션)")
    parser.add_argument("--limit", type=int, default=0, help="최근 N행만 (0은 전체)")
    parser.add_argument("--dry-run", action="store_true", help="S3 업로드 없이 로컬 통계만 출력")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_API_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        raise SystemExit("SUPABASE_URL 및 SUPABASE_API_KEY(또는 SERVICE_ROLE_KEY)가 필요합니다.")

    log(f"Supabase에서 데이터 수집 시작 (since={args.since_ts or 'ALL'}, limit={args.limit or 'all'})")
    ai_rows = fetch_table(
        supabase_url,
        supabase_key,
        "ai_outputs",
        select_cols="base_ts,embedding_a,embedding_b",
        order_col="base_ts",
        start_ts=args.since_ts,
        limit=args.limit,
    )
    sim_rows = fetch_table(
        supabase_url,
        supabase_key,
        "simulations_10m",
        select_cols="ts,trend_return_pct,mean_revert_return_pct,breakout_return_pct,scalper_return_pct,long_hold_return_pct,short_hold_return_pct",
        order_col="ts",
        start_ts=args.since_ts,
        limit=args.limit,
    )
    log(f"가져온 행: ai_outputs={len(ai_rows)}, simulations_10m={len(sim_rows)}")

    df = build_dataset(ai_rows, sim_rows)
    if df.empty:
        raise SystemExit("조인 결과가 없습니다. ts/기준 시점을 확인하세요.")

    log(f"조인 후 샘플 수: {len(df)} (컬럼: {list(df.columns)})")
    run_ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(":", "-")
    if args.dry_run:
        log("dry-run이므로 S3 업로드를 생략합니다.")
        return

    train_uri = save_parquet_to_s3(df, args.bucket, args.train_prefix, run_ts)
    latest_uri = save_latest_metadata(args.bucket, train_uri, len(df), run_ts, args.train_prefix)
    log(f"훈련 데이터 업로드 완료: {train_uri}")
    log(f"latest.json 갱신: {latest_uri}")


if __name__ == "__main__":
    main()
