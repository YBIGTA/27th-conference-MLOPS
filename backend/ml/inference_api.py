import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import requests
import torch
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from inference_loop import fetch_latest_embedding, log
from local_inference import download_and_extract, load_latest_uri, load_model, parse_embedding, predict_single


app = FastAPI(title="ML Inference API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PredictRequest(BaseModel):
    base_ts: Optional[str] = Field(None, description="ISO timestamp of embedding. If omitted, fetch latest from Supabase.")
    embedding_a: Optional[List[float]] = Field(None, description="256-dim embedding A")
    embedding_b: Optional[List[float]] = Field(None, description="256-dim embedding B")


class PredictResponse(BaseModel):
    base_ts: Optional[str]
    target_ts: Optional[str]
    model_uri: str
    pred: dict


@lru_cache()
def get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_API_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_API_KEY env vars are required.")
    return url, key


class ModelCache:
    def __init__(self):
        self.cached_uri: Optional[str] = None
        self.model = None
        self.meta = {}
        self.device = torch.device("cpu")
        self.cache_dir = Path(os.getenv("MODEL_CACHE_DIR", "/tmp/ml_api_cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def ensure_model(self, model_s3: str):
        target_uri = load_latest_uri(model_s3) if model_s3.endswith(".json") else model_s3
        if target_uri != self.cached_uri or self.model is None:
            log(f"loading model from {target_uri}")
            model_dir = download_and_extract(target_uri, self.cache_dir)
            self.model, self.meta = load_model(model_dir, self.device)
            self.cached_uri = target_uri
        return self.model, self.meta, self.cached_uri


cache = ModelCache()


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    model_s3 = os.getenv("MODEL_JSON", "s3://ybigta-mlops-landing-zone-324037321745/model/latest.json")
    model, meta, model_uri = cache.ensure_model(model_s3)

    # Fetch embeddings if not provided
    a = req.embedding_a
    b = req.embedding_b
    base_ts = req.base_ts
    if a is None or b is None:
        supabase_url, supabase_key = get_supabase()
        row = fetch_latest_embedding(supabase_url, supabase_key)
        if not row:
            raise HTTPException(status_code=404, detail="No embedding found in Supabase.")
        base_ts = row.get("base_ts")
        a = parse_embedding(row.get("embedding_a"))
        b = parse_embedding(row.get("embedding_b"))

    if not a or not b:
        raise HTTPException(status_code=400, detail="Embeddings missing or invalid.")

    preds = predict_single(model, a, b, cache.device)
    targets = meta.get("target_cols", [])
    pred_map = {targets[i] if i < len(targets) else f"t{i}": float(preds[i]) for i in range(len(preds))}

    # target_ts = base_ts + 10m if provided
    target_ts = None
    if base_ts:
        try:
            from datetime import datetime, timedelta, timezone

            dt = datetime.fromisoformat(base_ts.replace("Z", "+00:00")).astimezone(timezone.utc)
            target_ts = (dt + timedelta(minutes=10)).isoformat()
        except Exception:
            target_ts = None

    return PredictResponse(base_ts=base_ts, target_ts=target_ts, model_uri=model_uri, pred=pred_map)
