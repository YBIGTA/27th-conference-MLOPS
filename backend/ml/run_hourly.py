import argparse
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

import boto3
import fcntl
import sagemaker
from botocore.exceptions import ClientError
from sagemaker.pytorch import PyTorch


BASE_DIR = Path(__file__).resolve().parent
LOCK_PATH = BASE_DIR / ".pipeline.lock"
DEFAULT_BUCKET = os.getenv("LANDING_BUCKET", "ybigta-mlops-landing-zone-324037321745")
DEFAULT_TRAIN_PREFIX = os.getenv("TRAIN_PREFIX", "train")
DEFAULT_MODEL_PREFIX = os.getenv("MODEL_PREFIX", "model")


def log(msg: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    print(f"[run_hourly {now}] {msg}", flush=True)


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("다른 파이프라인 실행이 진행 중입니다. 잠시 후 다시 시도하세요.")
        yield
        fcntl.flock(f, fcntl.LOCK_UN)


def run_dataprep(bucket: str, train_prefix: str) -> None:
    cmd = [
        sys.executable,
        str(BASE_DIR / "data_prep.py"),
        "--bucket",
        bucket,
        "--train-prefix",
        train_prefix,
    ]
    log(f"실행: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def read_train_meta(bucket: str, train_prefix: str) -> Dict:
    key = f"{train_prefix.rstrip('/')}/latest.json"
    s3 = boto3.client("s3")
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as e:
        raise SystemExit(f"train latest.json을 읽지 못했습니다: {e}")
    meta = json.loads(body.decode("utf-8"))
    if "train_uri" not in meta:
        raise SystemExit("latest.json에 train_uri가 없습니다.")
    return meta


def start_training(
    role_arn: str,
    bucket: str,
    model_prefix: str,
    train_uri: str,
    instance_type: str,
    framework_version: str,
) -> Tuple[str, str]:
    session = sagemaker.Session()
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    job_name = f"mlops-returns-{run_ts}"
    train_channel = os.path.dirname(train_uri) + "/"
    output_path = f"s3://{bucket}/{model_prefix.rstrip('/')}/run_ts={run_ts}"
    estimator = PyTorch(
        entry_point="train.py",
        source_dir=str(BASE_DIR),
        role=role_arn,
        framework_version=framework_version,
        py_version="py310",
        instance_count=1,
        instance_type=instance_type,
        hyperparameters={"train-uri": train_uri},
        output_path=output_path,
        code_location=f"s3://{bucket}/code",
        disable_profiler=True,
        sagemaker_session=session,
    )
    log(f"훈련 잡 시작: {job_name} (input={train_channel}, output={output_path})")
    estimator.fit({"train": train_channel}, job_name=job_name, wait=True, logs=True)
    model_uri = estimator.model_data
    log(f"훈련 완료: job={job_name}, model_uri={model_uri}")
    return model_uri, job_name


def write_model_latest(bucket: str, model_prefix: str, model_uri: str, job_name: str, train_uri: str) -> str:
    key = f"{model_prefix.rstrip('/')}/latest.json"
    meta = {
        "model_uri": model_uri,
        "job_name": job_name,
        "train_uri": train_uri,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    body = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=body)
    return f"s3://{bucket}/{key}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="시간 기반 자동 학습 파이프라인 실행기")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--train-prefix", default=DEFAULT_TRAIN_PREFIX)
    parser.add_argument("--model-prefix", default=DEFAULT_MODEL_PREFIX)
    parser.add_argument("--instance-type", default=os.getenv("SM_TRAIN_INSTANCE", "ml.g4dn.xlarge"))
    parser.add_argument("--framework-version", default=os.getenv("SM_FRAMEWORK_VERSION", "2.2"))
    parser.add_argument("--skip-dataprep", action="store_true", help="Supabase→S3 적재 단계를 건너뜀")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    role_arn = os.getenv("SAGEMAKER_ROLE_ARN")
    if not role_arn:
        raise SystemExit("SAGEMAKER_ROLE_ARN 환경변수가 필요합니다.")

    with file_lock(LOCK_PATH):
        started_at = time.time()
        if not args.skip_dataprep:
            run_dataprep(args.bucket, args.train_prefix)

        train_meta = read_train_meta(args.bucket, args.train_prefix)
        train_uri = train_meta["train_uri"]

        model_uri, job_name = start_training(
            role_arn=role_arn,
            bucket=args.bucket,
            model_prefix=args.model_prefix,
            train_uri=train_uri,
            instance_type=args.instance_type,
            framework_version=args.framework_version,
        )
        latest_uri = write_model_latest(args.bucket, args.model_prefix, model_uri, job_name, train_uri)
        elapsed = int(time.time() - started_at)
        log(f"latest.json 업데이트 완료: {latest_uri} (elapsed {elapsed}s)")


if __name__ == "__main__":
    main()
