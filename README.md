## Bitcoin Trade Lake – High-Level Architecture

This repo captures a minimal reference implementation for collecting real-time Bitcoin trades on a public EC2 instance, persisting them into S3, and letting an orchestrator such as Airflow pick up the new batches directly from the landing bucket.

### Actors

| Actor | Responsibility |
| --- | --- |
| Ingest EC2 (public subnet) | Keeps a WebSocket to Binance, batches trades, uploads to S3 (`Binance/BTCUSDT/YYYY/MM/DD/HH/mm/`) |
| News Lambda (EventBridge) | Emits `Ext/{dataSource}/YYYY/MM/DD/news-*.json` heartbeat payloads to validate external ingestion paths |
| S3 bucket | Landing zone (`ybigta-mlops-landing-zone-<account>`) storing both Binance trades and other data sources |
| GitHub Actions + CodeDeploy | Pull latest GitHub commit, push it onto the EC2 Auto Scaling Group with zero manual SSH steps |
| Airflow / Downstream ETL | Uses S3 sensors to detect new partitions and launch analytics jobs (outside this repo) |

### Data Flow

1. **Stream capture** – `collector.py` maintains a WebSocket (Binance BTC/USDT by default) and writes normalized trades into an in-memory buffer.
2. **Batch flush** – every minute or when a batch reaches ~2 MB (whichever comes first) the buffer is converted into Parquet/GZIP CSV and written under `s3://ybigta-mlops-landing-zone-<acct>/Binance/BTCUSDT/YYYY/MM/DD/HH/mm/batch-<uuid>.parquet`.
3. **External replication guardrail** – an EventBridge-triggered Lambda drops `{timestamp}-News` objects under `s3://.../Ext/TEST/YYYY/MM/DD/` to validate Lambda ingestion paths before real news connectors are wired.
4. **Processing** – Downstream analytics (Airflow sensors, Glue crawlers, Batch jobs, etc.) react directly to the S3 partitions and process the referenced objects.

This design keeps the ingestion surface minimal (single EC2 with restricted egress) while giving orchestration tools a deterministic S3 layout to monitor.

### AWS Building Blocks

- **VPC**
  - Single public subnet for the ingest node with an Internet Gateway.
- **Security posture**
  - SSH ingress is locked to your CIDR list while egress is limited to HTTPS + DNS to satisfy the “collect & upload only” requirement.
- **IAM**
  - `role/ingestor` – permissions for S3 put/list plus CloudWatch Logs and CodeDeploy coordination.
  - `role/news-lambda` – scoped S3 write for `Ext/` payloads.
- **S3**
  - Landing bucket with versioning/SSE/lifecycle at `ybigta-mlops-landing-zone-<account>`.
  - Artifact bucket that stores CodeDeploy bundles uploaded by GitHub Actions.
- **Automation**
  - EventBridge rule + Lambda to mock external news feeds.
  - GitHub Actions (`.github/workflows/deploy.yml`) updates the Lambda code and triggers CodeDeploy so EC2 instances pick up new commits with no manual SSH.
  - SSM Parameter Store entry (`env_parameter_name`, default `/mlops/collector/env`) is fetched in user data so `collector.service` always has the latest `.env`.
- **Observability**
  - Use CloudWatch agent/Logs for collector + Lambda; add alarms for upload failure, Lambda errors, and Airflow DAG delays as needed.

### Repository Layout

```
infra/
├── ingestor/
│   ├── README.md              # Operational guide + .env description
│   ├── app/
│   │   ├── requirements.txt
│   │   └── collector.py
│   ├── codedeploy/
│   │   ├── collector.service
│   │   └── scripts/
│   │       ├── install.sh
│   │       ├── start.sh
│   │       └── stop.sh
│   ├── lambda/
│   │   └── news_ingestor/
│   │       └── main.py
│   └── terraform/
│       └── main.tf            # Minimal skeleton to provision core AWS resources
└── database/                  # Placeholder for downstream analytics infra
```
`appspec.yml` in the repo root wires the CodeDeploy lifecycle hooks to those scripts.

### Next Steps

1. Edit `infra/ingestor/app/.env.example`, render a real `.env`, and store it in SSM Parameter Store (default key `/mlops/collector/env`) so EC2 instances can download it at boot.
2. Deploy the Terraform module (or reproduce equivalent IaC) to provision networking, IAM, S3, Lambda, and CodeDeploy.
3. Configure GitHub repository secrets (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `CODEDEPLOY_APP_NAME`, `CODEDEPLOY_BUCKET`, `CODEDEPLOY_DEPLOYMENT_GROUP`, `NEWS_LAMBDA_NAME`) so `.github/workflows/deploy.yml` can push bundles and trigger CodeDeploy/Lambda updates on every `main` push.
4. After the stack is up, push to `main` and let GitHub Actions trigger CodeDeploy; Airflow/ETL jobs can watch the S3 prefixes to continue processing.

Feel free to extend this baseline with DMS/Kinesis/Glue if your throughput or compliance needs change.
