cd /Users/jaehun/Desktop/Github\ Projects/27th-conference-MLOPS
set -a && source infra/database/.env && set +a  # SUPABASE_URL/ENV_SECRET 로드

python - <<'PY'
import os, json, pandas as pd, requests

csv_path = "price_1s.csv"
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("ENV_SECRET")
if not url or not key:
    raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 필요")

df = pd.read_csv(csv_path, parse_dates=["ts"])
# 15초 버킷 CSV가 tz 포함일 수도 있어 UTC로 변환 후 ISO 문자열로 변환
df["ts"] = df["ts"].dt.tz_convert("UTC").dt.strftime("%Y-%m-%dT%H:%M:%SZ")
records = df.to_dict(orient="records")

endpoint = f"{url}/rest/v1/price_15s"
headers = {
    "apikey": key,
    "Authorization": f"Bearer {key}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

batch = 500
total = 0
for i in range(0, len(records), batch):
    chunk = records[i:i+batch]
    resp = requests.post(endpoint, headers=headers, data=json.dumps(chunk))
    if not resp.ok:
        raise SystemExit(f"Upsert failed at {i}: {resp.status_code} {resp.text}")
    total += len(chunk)

print(f"Upserted {total} rows from {csv_path}")
PY

