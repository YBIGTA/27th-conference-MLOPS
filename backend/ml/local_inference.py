import argparse
import json
import os
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import boto3
import numpy as np
import torch
import torch.nn as nn

TARGET_COLS = [
    "trend_return_pct",
    "mean_revert_return_pct",
    "breakout_return_pct",
    "scalper_return_pct",
    "long_hold_return_pct",
    "short_hold_return_pct",
]


def log(msg: str) -> None:
    print(f"[local_inference] {msg}")


def parse_embedding(raw: Any) -> Optional[List[float]]:
    if raw is None:
        return None
    if isinstance(raw, list):
        return [float(x) for x in raw]
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{") or s.startswith("["):
            s = s.strip("{}[]")
        if not s:
            return None
        try:
            return [float(x) for x in s.split(",") if x.strip() != ""]
        except Exception:
            return None
    return None


def load_latest_uri(latest_s3: str) -> str:
    s3 = boto3.client("s3")
    bucket, key = parse_s3(latest_s3)
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    meta = json.loads(body.decode("utf-8"))
    model_uri = meta.get("model_uri") or meta.get("artifact_uri")
    if not model_uri:
        raise ValueError("latest.json에 model_uri/artifact_uri가 없습니다.")
    return model_uri


def parse_s3(uri: str) -> (str, str):
    if not uri.startswith("s3://"):
        raise ValueError(f"S3 URI가 아닙니다: {uri}")
    without = uri[len("s3://") :]
    bucket, key = without.split("/", 1)
    return bucket, key


class MLP(nn.Module):
    def __init__(self, input_dim: int, hidden_dims: List[int], output_dim: int, dropout: float, use_layernorm: bool):
        super().__init__()
        layers: List[nn.Module] = []
        last_dim = input_dim
        for dim in hidden_dims:
            layers.append(nn.Linear(last_dim, dim))
            if use_layernorm:
                layers.append(nn.LayerNorm(dim))
            layers.append(nn.ReLU())
            if dropout > 0:
                layers.append(nn.Dropout(dropout))
            last_dim = dim
        layers.append(nn.Linear(last_dim, output_dim))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def download_and_extract(model_s3: str, workdir: Path) -> Path:
    s3 = boto3.client("s3")
    bucket, key = parse_s3(model_s3)
    local_tar = workdir / "model.tar.gz"
    s3.download_file(bucket, key, str(local_tar))
    with tarfile.open(local_tar, "r:gz") as tar:
        tar.extractall(path=workdir)
    return workdir


def load_model(model_dir: Path, device: torch.device) -> (nn.Module, Dict[str, Any]):
    metadata_path = model_dir / "metadata.json"
    if metadata_path.exists():
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    else:
        meta = {}
    hidden_dims = meta.get("hidden_dims", [256, 128, 64])
    dropout = float(meta.get("dropout", 0.15))
    use_layernorm = bool(meta.get("use_layernorm", False))
    input_dim = int(meta.get("feature_dim", 512))
    targets = meta.get("target_cols", TARGET_COLS)
    model = MLP(input_dim=input_dim, hidden_dims=hidden_dims, output_dim=len(targets), dropout=dropout, use_layernorm=use_layernorm)
    state_path = model_dir / "model.pth"
    state = torch.load(state_path, map_location=device)
    model.load_state_dict(state)
    model.to(device)
    model.eval()
    meta["target_cols"] = targets
    meta["feature_dim"] = input_dim
    return model, meta


def predict_single(model: nn.Module, a: List[float], b: List[float], device: torch.device) -> List[float]:
    vec = np.asarray(a + b, dtype=np.float32)
    if vec.shape[0] != len(a) + len(b):
        raise ValueError("임베딩 길이가 올바르지 않습니다.")
    with torch.no_grad():
        x = torch.from_numpy(vec).to(device)
        out = model(x)
    return out.cpu().numpy().astype(float).tolist()


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def format_output(base_ts: Optional[str], preds: Dict[str, float]) -> Dict[str, Any]:
    if not base_ts:
        return {"pred": preds}
    try:
        base_dt = datetime.fromisoformat(base_ts.replace("Z", "+00:00")).astimezone(timezone.utc)
        target_dt = (base_dt + timedelta(minutes=10)).isoformat()
    except Exception:
        base_dt = None
        target_dt = None
    return {"base_ts": base_ts, "target_ts": target_dt, "pred": preds}


def parse_embeddings_from_args(args: argparse.Namespace) -> List[Dict[str, Any]]:
    if args.jsonl:
        return load_jsonl(Path(args.jsonl))
    if not args.embedding_a or not args.embedding_b:
        raise ValueError("--embedding-a/--embedding-b 또는 --jsonl 중 하나는 필수입니다.")
    return [{"base_ts": args.base_ts, "embedding_a": json.loads(args.embedding_a), "embedding_b": json.loads(args.embedding_b)}]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="로컬 CPU 추론 유틸리티 (10분 주기)")
    parser.add_argument("--model-s3", required=True, help="model.tar.gz 또는 latest.json의 S3 URI")
    parser.add_argument("--base-ts", help="입력 임베딩의 base_ts (ISO). jsonl 사용 시 무시.")
    parser.add_argument("--embedding-a", help="임베딩 A (JSON 배열 문자열)")
    parser.add_argument("--embedding-b", help="임베딩 B (JSON 배열 문자열)")
    parser.add_argument("--jsonl", help="배치 입력 JSONL 경로. 각 줄에 embedding_a/embedding_b/base_ts")
    parser.add_argument("--output", help="결과를 저장할 로컬 파일 경로(JSON). 미지정 시 stdout")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model_uri = load_latest_uri(args.model_s3) if args.model_s3.endswith(".json") else args.model_s3
    device = torch.device("cpu")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        model_dir = download_and_extract(model_uri, tmp_dir)
        model, meta = load_model(model_dir, device)

        inputs = parse_embeddings_from_args(args)
        results: List[Dict[str, Any]] = []
        targets = meta.get("target_cols", TARGET_COLS)

        for row in inputs:
            a = parse_embedding(row.get("embedding_a"))
            b = parse_embedding(row.get("embedding_b"))
            if not a or not b:
                raise ValueError("임베딩 파싱 실패")
            preds = predict_single(model, a, b, device)
            pred_map = {t: preds[i] for i, t in enumerate(targets)}
            results.append(format_output(row.get("base_ts"), pred_map))

    if args.output:
        Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"결과 저장: {args.output}")
    else:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
