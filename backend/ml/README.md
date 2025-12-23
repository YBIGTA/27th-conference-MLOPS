# ML 파이프라인 개요

매 1시간마다 Supabase에서 `ai_outputs`와 `simulations_10m`을 읽어 시간 오프셋(`base_ts = ts - 10m`)으로 조인한 학습 세트를 S3에 적재하고, SageMaker GPU 학습 후 결과 모델을 S3 `/model` 프리픽스로 남깁니다. 10분마다 실행되는 로컬 추론은 backend EC2(t3.medium)에서 최신 모델을 내려받아 CPU로 예측합니다.

## 경로와 버킷
- 학습 데이터: `s3://ybigta-mlops-landing-zone-324037321745/train/run_ts=<ISO>/train.parquet`
- 모델 아티팩트: `s3://ybigta-mlops-landing-zone-324037321745/model/run_ts=<ISO>/model.tar.gz`
- 메타데이터: `.../latest.json`에 최신 run_ts와 체크포인트를 기록 (예: `{"model_uri":"s3://.../model/run_ts=.../model.tar.gz"}`)

## 환경 변수
- `SUPABASE_URL` – Supabase 프로젝트 URL
- `SUPABASE_API_KEY` – 읽기 전용(API 키)
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION`
- `LANDING_BUCKET` – 기본값 `ybigta-mlops-landing-zone-324037321745`
- 선택: `TRAIN_PREFIX`(기본 `train`), `MODEL_PREFIX`(기본 `model`)

## 설치
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r ml/requirements.txt
```

## 데이터 적재 (매시간)
```bash
cd backend
python ml/data_prep.py \
  --bucket ybigta-mlops-landing-zone-324037321745 \
  --train-prefix train \
  --limit 0          # 0이면 전체, >0이면 최근 N행만
```
- Supabase에서 `ai_outputs`와 `simulations_10m`을 가져와 `ts`와 `base_ts = ts - 10m`로 내부 조인.
- 512차원 입력(embedding_a 256 + embedding_b 256, 둘 다 합=1)을 리스트 형태로 parquet에 저장.
- S3에 `train.parquet`와 `latest.json` 업로드.

## 학습 (SageMaker Script Mode)
`ml/train.py`가 엔트리포인트입니다. 예시 하이퍼파라미터:
- 입력 512 → 256 → 128 → 64 → 6 (ReLU, Dropout 0.1~0.2)
- Adam(lr 1e-3), weight decay 1e-4, early stopping on val MSE/MAE
- 시계열 홀드아웃: train 70%, val 10%, test 20% (시간 오름차순 분할)
- 임베딩은 추가 스케일링 없이 사용(합=1), 모델 내부 LayerNorm 옵션으로 안정화

## 로컬 추론 (10분마다 backend EC2)
```bash
cd backend
python ml/local_inference.py \
  --model-s3 s3://ybigta-mlops-landing-zone-324037321745/model/latest.json \  # 또는 model.tar.gz 직접 경로
  --base-ts <2025-01-01T01:00:00Z> \
  --embedding-a "[0.1,0.1,...]" \
  --embedding-b "[...]"
```
- `latest.json`을 따라 최신 `model.tar.gz`를 내려받고 CPU로 예측합니다.
- 배치 입력을 파일(JSON Lines)로도 받을 수 있게 해 두었으니 cron에서 10분마다 실행하면 됩니다.

## 재학습 주기
- EventBridge `rate(1 hour)` 등으로 `data_prep.py` → SageMaker 학습 → `latest.json` 갱신 → EC2 측 cron이 새 모델을 감지해 재로딩하는 순서로 오케스트레이션하세요.

## 자동 실행 (1시간 주기)
- 필수 env: `SUPABASE_URL`, `SUPABASE_API_KEY`, `AWS_ACCESS_KEY_ID/SECRET/REGION`, `SAGEMAKER_ROLE_ARN`, `LANDING_BUCKET=ybigta-mlops-landing-zone-324037321745`
- 선택 env: `TRAIN_PREFIX=train`, `MODEL_PREFIX=model`, `SM_TRAIN_INSTANCE=ml.g4dn.xlarge`, `SM_FRAMEWORK_VERSION=2.2`
- 스케줄 실행 예시(cron):  
  `0 * * * * cd /path/to/27th-conference-MLOPS/backend && .venv/bin/python ml/run_hourly.py >> /var/log/ml_pipeline.log 2>&1`
- `ml/run_hourly.py`: 락을 잡고 `data_prep.py` 실행 → `train/latest.json`을 읽어 SageMaker PyTorch 학습(job 이름 `mlops-returns-YYYYMMDD-HHMMSS`) → 모델 경로를 `model/latest.json`에 반영. 필요 시 `--skip-dataprep`로 데이터 적재를 건너뛸 수 있음.
