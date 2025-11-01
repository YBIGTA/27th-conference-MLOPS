# Market Data Ingestion System

A minimal, robust real-time market data ingestion pipeline for Binance trade data. This system streams trade events via WebSocket and stores them directly to AWS S3 with automatic file rotation, compression, and manifest generation.

## Features

- **Real-time WebSocket streaming** from Binance trade API
- **Automatic file rotation** (2 MB or 5 seconds, whichever comes first)
- **Gzip compression** for efficient storage
- **S3 direct upload** with server-side encryption (AES256)
- **Manifest generation** for data quality tracking
- **Auto-reconnect** on connection failures
- **Fault-tolerant** multi-instance deployment
- **Systemd integration** for production deployment

## Architecture

```
Binance WebSocket → Collector → Local Buffer → Rotation → Compress → S3 Upload
                                                                    ↓
                                                          Manifest Generation
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed system design.

## Deployment Options

### Option 1: Automated Deployment with Terraform (Recommended)

**Fully automated**: Creates VPC, EC2 instances, S3 bucket, and starts collection automatically.

See **[docs/TERRAFORM_DEPLOYMENT.md](docs/TERRAFORM_DEPLOYMENT.md)** for complete guide.

**Quick steps:**
```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars - set bucket_name
terraform init
terraform apply
```

**Result**: 3x EC2 instances automatically start collecting data to S3 within 5 minutes.

### Want Multiple Symbols? (BTC, ETH, SOL, etc.)

Deploy additional symbols without affecting existing ones:

```bash
# Deploy BTCUSDT (first symbol)
./infra/terraform/deploy-symbol.sh BTCUSDT apply

# Add ETHUSDT (separate instances, no interference)
./infra/terraform/deploy-symbol.sh ETHUSDT apply

# Add more symbols
./infra/terraform/deploy-symbol.sh SOLUSDT apply
```

See **[MULTI_SYMBOL_DEPLOYMENT.md](docs/MULTI_SYMBOL_DEPLOYMENT.md)** for complete guide.

### Option 2: Local Development / Manual Deployment

For local testing or manual EC2 setup.

## Quick Start - Local Development (10 Minutes)

### Prerequisites

- Python 3.11+
- AWS account with S3 access
- AWS credentials configured (`aws configure` or IAM role)

### 1. Clone and Install

```bash
git clone <repository-url>
cd market-data-ingestion
pip install -r requirements.txt
```

### 2. Set Up Infrastructure

```bash
# Configure Terraform
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
nano terraform.tfvars  # Edit with your bucket name

# Create S3 bucket and IAM resources
terraform init
terraform apply
```

### 3. Configure Application

```bash
cd ../..
cp .env.example .env
nano .env  # Set RAW_BUCKET and other variables
```

Example `.env`:
```bash
RAW_BUCKET=my-market-data-bucket
SYMBOL=BTCUSDT
LOCAL_DIR=/tmp/market-data
ROT_BYTES=2097152
ROT_SECS=5
INSTANCE_ID=local-001
AWS_REGION=us-east-1
```

### 4. Run Collector

```bash
python -m src.collector.collector_simple
```

You should see:
```
2025-01-15 14:32:10 - collector - INFO - Starting market data collector
2025-01-15 14:32:10 - collector - INFO - Connecting to wss://stream.binance.com:9443/stream?streams=btcusdt@trade
2025-01-15 14:32:11 - collector - INFO - WebSocket connected successfully
2025-01-15 14:32:16 - collector - INFO - Rotating file: part-local-001-1736950336567.jsonl (1234 records)
2025-01-15 14:32:17 - collector - INFO - Successfully uploaded ...
```

### 5. Verify Data in S3

```bash
aws s3 ls s3://my-market-data-bucket/raw/exchange=binance/stream=trade/symbol=BTCUSDT/ --recursive
```

## Project Structure

```
market-data-ingestion/
├── src/
│   └── collector/
│       ├── collector_simple.py    # Main entry point
│       ├── ws_client.py           # Binance WebSocket client
│       ├── rotator.py             # File rotation logic
│       ├── s3_uploader.py         # S3 upload handler
│       ├── manifest.py            # Manifest generation
│       └── utils.py               # Utility functions
├── infra/
│   └── terraform/                 # Infrastructure as code
├── configs/
│   └── collector.yaml             # Configuration
├── schemas/
│   ├── trade.json                 # Trade event schema
│   └── manifest.json              # Manifest schema
├── docs/
│   ├── ARCHITECTURE.md            # System architecture
│   ├── S3_LAYOUT.md               # S3 data organization
│   └── RUNBOOK.md                 # Operations guide
├── tests/
│   ├── unit/                      # Unit tests
│   └── integration/               # Integration tests
├── systemd/
│   └── market-collector.service   # Systemd service
├── .env.example                   # Environment template
├── requirements.txt               # Python dependencies
└── README.md                      # This file
```

## Configuration

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `RAW_BUCKET` | S3 bucket name for raw data | - | Yes |
| `SYMBOL` | Trading symbol | `BTCUSDT` | No |
| `LOCAL_DIR` | Local temp directory | `/tmp/market-data` | No |
| `ROT_BYTES` | Max file size before rotation | `2097152` (2MB) | No |
| `ROT_SECS` | Max seconds before rotation | `5` | No |
| `INSTANCE_ID` | Instance identifier | `local-001` | No |
| `AWS_REGION` | AWS region | - | No |
| `LOG_LEVEL` | Logging level | `INFO` | No |

### Configuration File

Edit `configs/collector.yaml` for advanced settings:
- WebSocket reconnection behavior
- Retry logic
- Ping/pong intervals

## Production Deployment

### Automated Deployment (Recommended)

Use Terraform to automatically deploy 3 EC2 instances with full setup:

```bash
cd infra/terraform
terraform init
terraform apply
```

See **[docs/TERRAFORM_DEPLOYMENT.md](docs/TERRAFORM_DEPLOYMENT.md)** for details.

### Manual EC2 Deployment

If you prefer manual setup:

1. **Create infrastructure first:**
   ```bash
   cd infra/terraform
   terraform apply -target=aws_s3_bucket.raw_data -target=aws_iam_role.collector_role
   ```

2. **Launch EC2 instance** (t3.micro recommended)
3. **Attach IAM role** (created by Terraform)
4. **Deploy application:**

```bash
# On EC2 instance
git clone <repository-url>
cd market-data-ingestion
pip install -r requirements.txt
```

4. **Configure systemd service:**

```bash
# Edit service file with correct paths and environment
sudo nano systemd/market-collector.service

# Install and start service
sudo cp systemd/market-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable market-collector
sudo systemctl start market-collector
```

5. **Monitor service:**

```bash
sudo systemctl status market-collector
sudo journalctl -u market-collector -f
```

### Multi-Instance Setup

**Automated (Terraform)**:
- Set `instance_count = 3` in `terraform.tfvars`
- Run `terraform apply`
- Done! All instances auto-configured

**Manual**:
1. Launch 3 EC2 instances
2. Set unique `INSTANCE_ID` for each
3. All instances write to same S3 bucket
4. Duplicates are expected and acceptable

See [docs/RUNBOOK.md](docs/RUNBOOK.md) and [docs/TERRAFORM_DEPLOYMENT.md](docs/TERRAFORM_DEPLOYMENT.md) for details.

## S3 Data Layout

Data is organized in a hierarchical partition structure:

```
s3://<bucket>/raw/
  exchange=binance/
    stream=trade/
      symbol=BTCUSDT/
        dt=2025-01-15/14/32/
          part-i-001-1736950320567.jsonl.gz
          manifest-i-001-1736950320567.json
```

- **Partitioning:** UTC date/hour/minute
- **Format:** JSON Lines (`.jsonl`), gzip compressed
- **Manifest:** Metadata for each data file (record count, timestamps, hash)

See [docs/S3_LAYOUT.md](docs/S3_LAYOUT.md) for complete details.

## Testing

Run all tests:
```bash
pytest
```

Run specific test suites:
```bash
# Unit tests only
pytest tests/unit/

# Integration tests only
pytest tests/integration/

# Specific test file
pytest tests/unit/test_utils.py

# With coverage
pytest --cov=src
```

## Monitoring

### Service Status
```bash
sudo systemctl status market-collector
```

### View Logs
```bash
sudo journalctl -u market-collector -f
```

### Check S3 Uploads
```bash
aws s3 ls s3://<bucket>/raw/ --recursive | tail -20
```

### Monitor WebSocket
```bash
sudo journalctl -u market-collector | grep -i "connected"
```

## Troubleshooting

### Service Won't Start
- Check environment variables in `.env` or systemd service
- Verify AWS credentials: `aws s3 ls`
- Check logs: `sudo journalctl -u market-collector -n 50`

### WebSocket Connection Fails
- Test connectivity: `curl -I https://stream.binance.com`
- Check Binance API status
- Verify network/firewall rules

### Files Not Uploading
- Check IAM permissions: `aws s3 cp test.txt s3://<bucket>/`
- Verify bucket exists: `aws s3 ls s3://<bucket>/`
- Check bucket region matches `AWS_REGION`

See [docs/RUNBOOK.md](docs/RUNBOOK.md) for comprehensive troubleshooting guide.

## Performance

### Expected Throughput
- **Trades/sec:** 1,000-3,000 (per symbol)
- **Data rate:** ~200 KB/s compressed
- **Files/hour:** ~720 (5-second rotation)

### Resource Usage
- **CPU:** <10% (t3.micro)
- **Memory:** <200 MB
- **Disk:** <100 MB (transient)
- **Network:** <1 Mbps

## Data Quality

Each uploaded file includes a manifest JSON with:
- Record count
- Time range (min/max timestamps)
- Trade ID range (first/last)
- File sizes (compressed/uncompressed)
- SHA256 hash for integrity verification

## Security

- **Encryption:** S3 server-side encryption (AES256)
- **IAM:** Least-privilege roles (PutObject only)
- **Network:** TLS for all connections
- **Access:** No public bucket access

## Future Enhancements

Post-MVP features planned:
- Kinesis + Lambda for 1-second OHLCV aggregation
- Gap recovery via REST API
- Multi-symbol parallel collection
- CloudWatch metrics and alerting
- Docker/ECS deployment
- Athena integration for queries

## Documentation

- **[TERRAFORM_DEPLOYMENT.md](docs/TERRAFORM_DEPLOYMENT.md)** - Automated deployment guide ⭐
- **[MULTI_SYMBOL_DEPLOYMENT.md](docs/MULTI_SYMBOL_DEPLOYMENT.md)** - Deploy multiple symbols (BTC, ETH, etc.) ⭐
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** - System design and components
- **[S3_LAYOUT.md](docs/S3_LAYOUT.md)** - Data organization and format
- **[RUNBOOK.md](docs/RUNBOOK.md)** - Operations and troubleshooting
- **[PROJECT.md](PROJECT.md)** - AI development guide (original spec)

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes with tests
4. Submit pull request

## License

MIT License - see LICENSE file for details

## Support

For issues and questions:
- GitHub Issues: <repository-url>/issues
- Documentation: [docs/](docs/)

## Acknowledgments

Built for reliable, fault-tolerant market data collection with minimal complexity.
