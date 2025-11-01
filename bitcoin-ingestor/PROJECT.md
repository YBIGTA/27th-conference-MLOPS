# 🧭 AI Development Guide — Market Data Ingestion (S3-Only MVP)

## 🎯 Project Overview

This project implements an **MVP (minimum viable product)** for a **real-time Binance market-data ingestion system**.
The system connects to Binance WebSocket streams (e.g., `@trade`) and **stores raw data directly to AWS S3**.

No Kinesis, Lambda, or database components are included in this phase.
The goal is a minimal, reliable pipeline that **writes every event to S3 safely and continuously.**

**Core Objectives**

* Subscribe to Binance `@trade` stream in real time
* Rotate local files every **2 MB or 5 seconds**, whichever comes first
* Compress data (`.jsonl.gz`) and upload to S3
* Create a **manifest JSON** per file (record count, time range, hash, etc.)
* Allow multiple EC2 micro instances (e.g., 3) to run in parallel with duplicate-tolerant design

---

## 🏗️ Repository Structure

```
market-data-ingestion/
├── README.md
├── AI_DEV_GUIDE.md               # ← this file
├── .env.example
├── pyproject.toml or requirements.txt
│
├── configs/
│   └── collector.yaml
│
├── src/
│   └── collector/
│       ├── collector_simple.py
│       ├── rotator.py
│       ├── s3_uploader.py
│       ├── manifest.py
│       ├── ws_client.py
│       └── utils.py
│
├── systemd/
│   └── market-collector.service
│
├── infra/
│   └── terraform/
│       ├── main.tf
│       ├── providers.tf
│       ├── s3.tf
│       └── outputs.tf
│
├── schemas/
│   ├── trade.json
│   ├── manifest.json
│   └── ohlcv1s.parquet.schema
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── S3_LAYOUT.md
│   └── RUNBOOK.md
│
└── tests/
    ├── unit/
    └── integration/
```

---

## ⚙️ Technical Specifications

### 1️⃣ S3 Folder & File Layout

```
s3://<RAW_BUCKET>/raw/
  exchange=binance/
    stream=trade/
      symbol=BTCUSDT/
        dt=YYYY-MM-DD/HH/MM/
          part-<instance>-<epoch_ms>.jsonl.gz
          manifest-<instance>-<epoch_ms>.json
```

* **Compression:** gzip (`.jsonl.gz`)
* **Rotation Rule:** rotate when file size ≥ 2 MB **or** elapsed ≥ 5 seconds
* **Partitioning:** UTC-based time partitions
* **Manifest:** JSON file describing each uploaded chunk

  * includes record count, time_min/max, id_first/last, sha256, s3_key, etc.

---

### 2️⃣ `collector_simple.py`

* Connects to Binance WebSocket (`wss://stream.binance.com:9443/stream?streams=<symbol>@trade`)
* Asynchronous (asyncio) main loop
* Appends messages to local `.jsonl` file
* On rotation trigger:

  * Gzip-compress local file
  * Upload to S3 (`ServerSideEncryption=AES256`)
  * Generate and upload manifest JSON
  * Delete local temp files
* Reconnects automatically on error with 1-second backoff
* Required environment variables:

  * `RAW_BUCKET`, `SYMBOL`, `LOCAL_DIR`, `ROT_BYTES`, `ROT_SECS`, `INSTANCE_ID`

---

### 3️⃣ Manifest JSON Structure

```json
{
  "version": "1",
  "source": {
    "exchange": "binance",
    "stream": "trade",
    "symbol": "BTCUSDT",
    "instance_id": "i-0abc12345"
  },
  "payload": {
    "s3_key": "raw/.../part-i-0abc12345-1739141034567.jsonl.gz",
    "record_count": 12873,
    "bytes_uncompressed": 3689452,
    "bytes_gzip": 2048123,
    "time_min_ms": 1739141030123,
    "time_max_ms": 1739141034560,
    "id_first": 1234567890,
    "id_last": 1234580762,
    "sha256": "..."
  },
  "created_at_ms": 1739141034570
}
```

---

### 4️⃣ `rotator.py`

Defines a `RotatingWriter` class that:

* Writes events to file (`write(event)`)
* Checks rotation condition (`size ≥ ROT_BYTES` **or** `elapsed ≥ ROT_SECS`)
* On rotation: compress → upload to S3 → generate manifest → reset state

---

### 5️⃣ `s3_uploader.py`

Handles S3 uploads using boto3:

* `upload_file()` with `ServerSideEncryption="AES256"`
* Retries up to 3 times with exponential backoff
* Graceful exception logging

---

### 6️⃣ `systemd/market-collector.service`

```ini
[Service]
User=ec2-user
Restart=always
RestartSec=5
Environment=RAW_BUCKET=my-raw-bucket
Environment=SYMBOL=BTCUSDT
ExecStart=/usr/bin/python3 /home/ec2-user/collector_simple.py
```

* Automatic restart on crash
* Logs accessible via `journalctl -u market-collector -f`

---

### 7️⃣ Terraform (infra/terraform)

Creates an S3 bucket with:

* Versioning enabled
* Server-side encryption (AES256)
* Lifecycle policy (optional: archive to Glacier)

Outputs: `raw_bucket_name`, `bucket_arn`

---

### 8️⃣ Testing

* **Unit tests:** rotation logic, manifest creation
* **Integration tests:** mocked S3 upload (use `moto`)
* Run via `pytest`

---

## 🧩 Development Rules

| Topic          | Guideline                                          |
| -------------- | -------------------------------------------------- |
| Language       | Python 3.11, asyncio, boto3                        |
| Code style     | PEP8, type hints, logging                          |
| Testing        | pytest + moto for mock S3                          |
| Error handling | retries, reconnect, no silent fails                |
| Performance    | assume ≤ 3 MB/s per symbol                         |
| Security       | AES256 encryption, no plaintext secrets            |
| Duplicates     | allowed across instances; dedup handled downstream |
| Infra          | EC2 t3.micro × 3, IAM least privilege              |
| Logging        | stdout → journalctl → (optional) CloudWatch        |

---

## ✅ Definition of Done

* [ ] `collector_simple.py` successfully uploads `.jsonl.gz` and `.json` manifest to S3
* [ ] File rotation occurs exactly every 2 MB or 5 s
* [ ] S3 bucket shows proper partition layout
* [ ] Systemd restart resumes upload without loss
* [ ] Quickstart in README can run end-to-end within 10 minutes

---

## 🧱 Future Extensions (post-MVP)

* Add Kinesis + Lambda for 1-second OHLCV aggregation
* Gap recovery via REST API (`/api/v3/aggTrades?fromId=`)
* Athena / Glue integration
* Multi-symbol parallel ingestion
* Docker / ECS deployment

---

## 🔒 Summary

> Connect to Binance → collect JSON events → rotate (2 MB or 5 s) → gzip → upload to S3 + manifest.
> System runs redundantly across 3 micro instances; duplicates are acceptable, loss is not.
> Keep the implementation minimal, robust, and within this specification — **no external databases or pipelines**.

---

✅ **File name:** `AI_DEV_GUIDE.md`
Include this file in the repository root so any AI or human developer can follow the exact requirements when generating code.
