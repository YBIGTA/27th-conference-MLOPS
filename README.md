# AI Crypto Model Selector HTS

> 15초·10분 봉 가격과 최신 임베딩을 활용해 6개 알고리즘(Trend, Mean Revert, Breakout, Scalper, Long/Short Hold)의 10분 수익률을 예측하고, 주기적 재훈련/추론을 자동화하는 시스템

---

## 1. 프로젝트 개요 (Overview)

### 배경
- 뉴스/텍스트 임베딩과 시계열 가격 데이터를 함께 활용해 단기(10분) 전략별 예상 수익을 빠르게 산출하고자 함.
- 기존 ML/DL 기반 매매 방법론의 수동 백테스트/전략 선택 과정의 비효율, 재훈련/배포 자동화 부재.

### 목표
- 입력: Supabase의 임베딩(512차원, ai_outputs) + 가격/시뮬레이션 피처(15s/10m).
- 처리: SageMaker에서 주기적 학습(시간당), 최신 모델을 S3로 배포 후 로컬 추론 컨테이너에서 10분 주기로 예측.
- 출력: 각 전략의 10분 수익률 추정치와 상태 로그를 프런트에 제공.

---

## 2. 문제 정의 (Problem Statement)

- 10분 후 전략별 수익률을 다중 타깃으로 예측하는 모델을 구축하고, 재훈련-배포-추론 파이프라인을 자동화한다.
- 사용자: 단기 전략 선택이 필요한 트레이더/서비스.
- 목표: 최신 임베딩과 가격을 활용한 실시간 예측 제공, 재훈련 주기 1시간 내 자동화.

---

## 3. 전체 시스템 구조 (System Architecture)

```
[Supabase 임베딩/시뮬레이션/가격]  →  [데이터 전처리 & S3 업로드]  →  [SageMaker 학습]
                                                                    ↓
                                                   [모델 아티팩트 S3 저장 (/model/latest.json)]
                                                                    ↓
                                      [로컬 추론 컨테이너(ml-infer / ml-api) 10분 주기 예측]
                                                                    ↓
                                            [FastAPI 백엔드 + 프런트 대시보드 표시]
```

주요 컴포넌트
- 데이터 인입/전처리: Supabase → S3(/train).
- 학습: SageMaker PyTorch Estimator, 다중타깃 회귀.
- 추론: 로컬 Docker 컨테이너(ml-infer 주기 실행, ml-api 수동 호출).
- 프런트: 실시간 차트, 예측 결과, 로그/뉴스 표시.

---

## 4. 기술 스택 (Tech Stack)

### Backend / Core
- Language: Python 3.11
- Framework: FastAPI + Uvicorn
- Model: PyTorch 다중 타깃 회귀(입력 512차원 임베딩 concat, 출력 6개 전략 수익률)

### System / Infra
- OS: Linux (EC2)
- Hardware: CPU 추론(t3.medium), GPU 학습(SageMaker, 가용 시)
- Container / Deployment: Docker, docker-compose, AWS SageMaker Training
- IaC: Terraform

### Data / Storage
- Dataset: Supabase 테이블(ai_outputs, price_15s, simulations_10m)
- DB / File Format: Postgres(Supabase), Parquet on S3(/train), 모델 아티팩트 S3(/model)

---

## 5. 데이터 설명 (Dataset)

| 항목 | 설명 |
|----|----|
| 데이터 수 | 약 1,500 샘플(10분 단위 학습용) |
| 입력 형태 | 512차원 임베딩(256+256 concat), 가격/시뮬레이션 피처 |
| 출력 형태 | 6개 전략의 10분 수익률(trend/mean_revert/breakout/scalper/long_hold/short_hold) |
| 전처리 | 임베딩 합 1로 스케일링, 시간 정렬 후 10분 시프트 라벨링, Parquet 업로드 |

---

## 6. 핵심 방법론 (Methodology)

- 특징: 최신 임베딩(텍스트) + 직전 가격/시뮬레이션 피처를 결합해 10분 후 수익률을 예측.
- 모델: PyTorch MLP 계열 다중 회귀(입력 512차원, 출력 6차원).
- 파이프라인: 데이터 수집→Parquet 생성→S3 업로드→SageMaker 학습→S3 모델→로컬 추론 컨테이너에서 캐싱/예측.

---

## 7. 실험 및 결과 (Experiments & Results)

| Metric | Result |
|------|--------|
| 참고 | 현재 프로덕션용 파이프라인 구성 완료, 세부 지표는 추가 기록 예정 |

---

## 8. 실행 방법 (How to Run)

```bash
# 기본 요구: Docker, docker-compose, AWS 자격증명(SageMaker/S3), Supabase URL/API Key

# 1) 환경 변수 준비
#    backend/.env, frontend/.env에 Supabase, S3, SageMaker Role/Region, API URL 설정

# 2) 빌드 및 실행 (백엔드/추론 컨테이너)
cd backend
docker compose up -d --build backend ml-runner ml-infer ml-api

# 3) 프런트 실행
cd ../frontend
npm install
npm run dev   # 또는 npm run build && npm run preview
```

- 주요 옵션: `ML_INTERVAL_SECONDS`, `INFER_INTERVAL_SECONDS`, `MODEL_JSON`(최신 모델 메타 S3 경로) 등 환경변수로 제어.

---

## 9. 프로젝트 구조 (Directory Structure)
```
.
├── backend/          # FastAPI, Dockerfile, docker-compose 포함
├── frontend/         # Vite + React 대시보드
├── ml/               # 학습/추론 스크립트, SageMaker 엔트리포인트
├── infra/            # Terraform for backend/SageMaker roles/VPC 등
├── simulation/       # 시뮬레이션/보조 스크립트
└── README.md
```

---

## 10. 한계점 및 향후 계획 (Limitations & Future Work)
- SageMaker GPU 쿼터/권한 제약 시 대체 인스턴스 자동 선택 필요.
- 지표/모니터링 대시보드(모델 드리프트, 예측 대비 실적) 고도화.
- 더 긴 시계열 맥락과 추가 피처(거래량, 온체인 지표) 반영 검토.

---

## 11. 팀 구성 및 역할 (Team)
- 팀장(손재훈): 전체 설계 및 Frontend/Backend/ML pipline 설계/Binance API consumer 로직 작성
- 팀원(김예찬): Kafka를 이용한 뉴스 API 서빙 (https://github.com/yechance7/kafka-consumer)
- 팀원(양인혜): 데이터베이스 설계 및 데이터 프로세싱 파이프라인 구축
- 팀원(조요셉): 전체 설계 및 PM
- 팀원(이지용): 모델 시뮬레이션을 위한 데이터 프로세싱, AI 로직 구축

