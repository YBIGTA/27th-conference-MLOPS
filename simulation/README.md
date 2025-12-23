### Simulation prefill (ai_outputs)
- 위치: `simulation/prefill.py`
- 필요 env: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`(insert용), `OPENAI_API_KEY` (선택으로 `GPT_MODEL=gpt-5-mini`, `EMBED_MODEL=text-embedding-3-small`).
- `.env` 자동 로드: `simulation/.env` → 현재 작업 디렉터리의 `.env` 순서로 읽어 미설정 값만 주입.
- 기본 동작: `base_ts`마다 10분(40개의 15s 캔들)과 직전 10일 일봉 + 최신 뉴스 6개를 요약(최대 4문장, 짧은 답변)해 `ai_outputs`에 upsert(`base_ts` 기준).
- 실행 예시:
  ```bash
  source backend/.venv/bin/activate  # 또는 원하는 venv
  python simulation/prefill.py --from-ts 2024-01-01T00:00:00Z --to-ts 2024-01-01T01:00:00Z
  ```
- `--from-ts/--to-ts`가 없으면 현재 시각을 10분 단위로 내림해 한 번 실행. 실패한 윈도우는 로그만 남기고 계속 진행.
- 데이터가 부족한 경우: 기본은 10분(40개) 캔들이 모두 있어야 진행합니다. 부족한 창을 강제로 진행하려면 `--min-price-rows`를 낮추세요 (예: `--min-price-rows 20`).
- 참고: 일부 OpenAI 모델은 `temperature` 조정이 불가해 기본값(1)로 호출합니다.
- 진행 로그 CSV: 기본 `simulation/prefill_log.csv`에 `status`/`reason`/요약 텍스트를 기록합니다. 경로를 바꾸려면 `--csv-path /path/to/log.csv`, 비활성화하려면 빈 문자열(`--csv-path ""`).
- 진행 로그 JSONL: 기본 `simulation/prefill_log.jsonl`에 각 윈도우 결과를 JSON line으로 기록하며 임베딩도 포함합니다. 경로 변경 `--json-path /path/to/log.jsonl`, 비활성화는 빈 문자열(`--json-path ""`).

### Simulation prefill (simulations_10m)
- 위치: `simulation/strategy_prefill.py`
- 필요 env: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`(또는 `SUPABASE_API_KEY`).
- 동작: 10분 창(기본 40개 15s 캔들)을 사용해 각 전략(trend, mean_revert, breakout, scalper, long_hold, short_hold)의 수익률을 계산 후 `simulations_10m` 테이블에 upsert. 로컬 CSV 로그(`simulation/simulations_10m.csv`)에도 기록.
- 실행 예시:
  ```bash
  source backend/.venv/bin/activate
  python simulation/strategy_prefill.py --from-ts 2025-12-11T03:00:00Z --to-ts 2025-12-22T01:50:00Z
  ```
- 옵션: `--min-price-rows`(기본 40, 부족하면 skip), `--csv-path`(빈 문자열이면 로그 비활성화), `--sleep-seconds`(윈도우 간 대기).
