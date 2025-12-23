## Gap Fill Backend

FastAPI 서비스 (SSE)로 Supabase `price_15s`를 부트스트랩하고, Binance aggTrades를 15초봉으로 변환해 갭을 메우는 백엔드입니다. EC2(t3.medium)에서 Docker로 돌릴 전제를 둡니다. **동일 EC2에서 ML 재학습/배포 파이프라인(시간당)도 `backend/ml`에 있는 코드로 빌드된 ml-runner 컨테이너로 자동 실행**합니다.

### 주요 엔드포인트
- `POST /session/start` – Supabase JWT 검증 → `supabase_last_ts`, `stream_url`, `bootstrap_url` 반환.
- `GET /bootstrap` – 최신→과거 5k 페이지네이션(커서). 쿼리: `cursor`, `limit`, `from_ts`, `to_ts`.
- `GET /stream/gap` – SSE로 15초봉 전송. 파라미터: `session_id`, `from_ts`, `to_ts`(선택, 없으면 `GAP_STREAM_MAX_MINUTES` 기본).
- `POST /session/stop` – 세션 종료.

### 환경 변수
- 공통: `SUPABASE_URL` (필수), `SUPABASE_API_KEY` 또는 `SUPABASE_ANON_KEY` (필수)
- `SUPABASE_ALLOW_SUB` (선택, 개발용 allowlist)
- `BINANCE_SYMBOL` (기본 `BTCUSDT`)
- `BOOTSTRAP_PAGE_LIMIT` (기본 5000), `GAP_STREAM_MAX_MINUTES` (기본 15), `SESSION_TTL_SECONDS` (기본 1800), `GAP_STREAM_SLEEP_SECONDS` (기본 1.5)
- ML 파이프라인(ml-runner 컨테이너):
  - `LANDING_BUCKET` (기본 `ybigta-mlops-landing-zone-324037321745`)
  - `TRAIN_PREFIX`/`MODEL_PREFIX` (기본 `train`/`model`)
  - `SAGEMAKER_ROLE_ARN` (SageMaker 학습용 역할 ARN, iam:PassRole 허용 필요)
  - `SM_TRAIN_INSTANCE` (기본 `ml.g4dn.xlarge`), `SM_FRAMEWORK_VERSION` (기본 `2.2`)
- `ML_INTERVAL_SECONDS` (기본 3600, 1시간마다 `ml/run_hourly.py` 실행)
- 추론용: `INFER_INTERVAL_SECONDS` (기본 600, 10분마다 최신 임베딩 추론), `MODEL_JSON` (기본 model/latest.json S3 경로)

### 로컬 실행
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Docker 이미지 빌드/실행
```bash
cd backend
docker build -t gap-backend:local .
docker run --rm -p 8000:8000 \
  -e SUPABASE_URL=... \
  -e SUPABASE_API_KEY=... \
  gap-backend:local
```

### Docker Compose (백엔드 + ML 러너)
```bash
# /opt/backend/.env 에 위 환경변수를 채워둔 상태
cd backend
docker compose --env-file /opt/backend/.env up -d --build
```
- `backend` 서비스: FastAPI.
- `ml-runner` 서비스: `ml/run_hourly.py`를 기본 1시간 주기로 실행해 Supabase→S3 데이터 적재 후 SageMaker 학습을 트리거하고 `/model/latest.json`을 갱신.
- `ml-infer` 서비스: 기본 10분마다(ENV `INFER_INTERVAL_SECONDS`) Supabase `ai_outputs` 최신 임베딩을 읽어 `model/latest.json` 기준으로 로컬 CPU 추론 후 로그로 출력.
- 주기 조정: `/opt/backend/.env`에 `ML_INTERVAL_SECONDS=900`(예: 15분), `INFER_INTERVAL_SECONDS=300` 등으로 설정.

### EC2에서 (Terraform user-data는 Docker만 설치)
1. SSH 접속 후 `.env` 준비(SSM에서 내려온 `/opt/backend/.env`를 `--env-file`로 활용 가능).
2. Compose로 둘 다 실행:
   ```bash
   cd /opt/backend   # artifact를 이 위치에 배포했다고 가정
   docker compose --env-file /opt/backend/.env up -d --build
   ```
3. 필요 시 `backend/` 폴더를 zip으로 S3에 올려 user-data가 내려받게 할 수 있습니다(옵션).
