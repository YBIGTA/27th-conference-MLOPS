# Operational Runbook

## Quick Start

### Prerequisites
- AWS account with S3 access
- Python 3.11+
- AWS credentials configured
- (Optional) EC2 instance with IAM role

### Initial Setup

1. **Clone Repository**
   ```bash
   git clone <repository-url>
   cd market-data-ingestion
   ```

2. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment**
   ```bash
   cp .env.example .env
   # Edit .env with your settings
   nano .env
   ```

4. **Create S3 Infrastructure**
   ```bash
   cd infra/terraform
   cp terraform.tfvars.example terraform.tfvars
   # Edit terraform.tfvars
   nano terraform.tfvars

   terraform init
   terraform plan
   terraform apply
   ```

5. **Run Collector Locally**
   ```bash
   python -m src.collector.collector_simple
   ```

## Deployment

### EC2 Deployment

1. **Launch EC2 Instance**
   ```bash
   # Use t3.micro or t4g.micro
   # Attach IAM role created by Terraform
   # Amazon Linux 2023 or Ubuntu 22.04
   ```

2. **Install Python 3.11**
   ```bash
   # Amazon Linux 2023
   sudo dnf install python3.11 python3.11-pip -y

   # Ubuntu 22.04
   sudo apt update
   sudo apt install python3.11 python3.11-venv -y
   ```

3. **Deploy Application**
   ```bash
   # Copy application to EC2
   scp -r . ec2-user@<instance-ip>:~/market-data-ingestion/

   # SSH to instance
   ssh ec2-user@<instance-ip>

   # Install dependencies
   cd market-data-ingestion
   pip3.11 install -r requirements.txt
   ```

4. **Configure Systemd Service**
   ```bash
   # Edit service file with correct paths and environment
   sudo nano systemd/market-collector.service

   # Install service
   sudo cp systemd/market-collector.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable market-collector
   sudo systemctl start market-collector
   ```

5. **Verify Operation**
   ```bash
   sudo systemctl status market-collector
   sudo journalctl -u market-collector -f
   ```

### Multiple Instances

Deploy 3 instances for redundancy:

1. Launch 3 EC2 instances with unique names
2. Set unique `INSTANCE_ID` for each (use EC2 instance ID)
3. Deploy and start service on all instances
4. Verify all instances are uploading to S3

## Monitoring

### Check Service Status
```bash
sudo systemctl status market-collector
```

### View Logs
```bash
# Follow logs in real-time
sudo journalctl -u market-collector -f

# View recent logs
sudo journalctl -u market-collector -n 100

# Filter by time
sudo journalctl -u market-collector --since "1 hour ago"
```

### Check S3 Uploads
```bash
# List recent files
aws s3 ls s3://<bucket>/raw/exchange=binance/stream=trade/symbol=BTCUSDT/ --recursive | tail -20

# Check file sizes
aws s3 ls s3://<bucket>/raw/exchange=binance/stream=trade/symbol=BTCUSDT/ --recursive --summarize

# Download and inspect a file
aws s3 cp s3://<bucket>/raw/.../part-....jsonl.gz - | gunzip | head
```

### Monitor WebSocket Connection
```bash
# Check for connection messages
sudo journalctl -u market-collector | grep -i "connected"

# Check for disconnection/reconnection
sudo journalctl -u market-collector | grep -i "reconnect"
```

### Monitor Upload Performance
```bash
# Check upload success rate
sudo journalctl -u market-collector | grep -i "successfully uploaded" | wc -l

# Check upload failures
sudo journalctl -u market-collector | grep -i "failed to upload"
```

## Troubleshooting

### Issue: Service Won't Start

**Symptoms:**
- `systemctl status` shows "failed"
- Service exits immediately

**Diagnosis:**
```bash
sudo journalctl -u market-collector -n 50
```

**Common Causes:**
1. Missing environment variables
   - Check `.env` file or systemd Environment settings
   - Ensure `RAW_BUCKET` is set

2. AWS credentials not configured
   - Check IAM role attached to EC2
   - Verify `aws s3 ls` works

3. Python dependency missing
   - Reinstall: `pip install -r requirements.txt`

### Issue: WebSocket Connection Fails

**Symptoms:**
- Logs show repeated connection attempts
- "WebSocket error" messages

**Diagnosis:**
```bash
sudo journalctl -u market-collector | grep -i "websocket"
```

**Solutions:**
1. Check network connectivity
   ```bash
   curl -I https://stream.binance.com
   ```

2. Verify Binance API status
   - Check https://www.binance.com/en/support/announcement

3. Test WebSocket manually
   ```bash
   pip install websockets
   python3 -c "
   import asyncio
   import websockets

   async def test():
       async with websockets.connect('wss://stream.binance.com:9443/stream?streams=btcusdt@trade') as ws:
           msg = await ws.recv()
           print(msg)

   asyncio.run(test())
   "
   ```

### Issue: Files Not Uploading to S3

**Symptoms:**
- Service running but no files in S3
- "Failed to upload" errors in logs

**Diagnosis:**
```bash
sudo journalctl -u market-collector | grep -i "upload"
aws s3 ls s3://<bucket>/raw/
```

**Solutions:**
1. Check IAM permissions
   ```bash
   # Test upload manually
   echo "test" > /tmp/test.txt
   aws s3 cp /tmp/test.txt s3://<bucket>/test.txt
   ```

2. Verify bucket name
   - Check `RAW_BUCKET` environment variable
   - Ensure bucket exists: `aws s3 ls s3://<bucket>/`

3. Check bucket region
   - Ensure `AWS_REGION` matches bucket region

### Issue: High Memory Usage

**Symptoms:**
- Process memory grows over time
- System OOM killer terminates process

**Diagnosis:**
```bash
ps aux | grep collector
top -p $(pgrep -f collector)
```

**Solutions:**
1. Reduce rotation threshold
   - Lower `ROT_BYTES` to 1 MB
   - Lower `ROT_SECS` to 3 seconds

2. Ensure files are cleaned up
   - Check local directory: `ls -lh /tmp/market-data/`
   - Remove orphaned files

### Issue: Duplicate Data

**Symptoms:**
- Same trade IDs appearing multiple times

**Expected Behavior:**
- Multiple instances will create duplicates
- This is by design for fault tolerance
- Deduplication should be handled downstream

**Verification:**
- Check `instance_id` in filenames
- Verify different instances are creating files

## Maintenance

### Update Application
```bash
# Stop service
sudo systemctl stop market-collector

# Pull updates
cd ~/market-data-ingestion
git pull

# Install any new dependencies
pip3.11 install -r requirements.txt

# Restart service
sudo systemctl start market-collector
sudo systemctl status market-collector
```

### Rotate Logs
Systemd handles log rotation automatically through journald.

To limit journal size:
```bash
sudo journalctl --vacuum-size=500M
sudo journalctl --vacuum-time=7d
```

### Clean Local Files
If orphaned files accumulate:
```bash
# Stop service first
sudo systemctl stop market-collector

# Clean local directory
rm -rf /tmp/market-data/*

# Restart service
sudo systemctl start market-collector
```

### Restart Service
```bash
sudo systemctl restart market-collector
sudo systemctl status market-collector
```

## Backup & Recovery

### Configuration Backup
```bash
# Backup environment and service config
tar -czf config-backup-$(date +%Y%m%d).tar.gz \
  .env systemd/market-collector.service
```

### Data Recovery
- Data is already in S3 (redundant storage)
- If instance fails, launch new instance and redeploy
- No data loss (S3 is source of truth)

### Gap Recovery (Future)
Currently, gaps due to downtime are acceptable (duplicates across instances).
Future versions will include REST API fallback for gap recovery.

## Performance Tuning

### Optimize Rotation
- Adjust `ROT_BYTES` and `ROT_SECS` based on trade volume
- Higher values = fewer files, larger uploads
- Lower values = more files, smaller uploads

### Increase Instances
- Add more instances for higher availability
- Distribute across availability zones
- Monitor total S3 costs

### CloudWatch Integration (Future)
- Send metrics to CloudWatch
- Create alarms for failures
- Dashboard for monitoring

## Emergency Procedures

### Stop Collection
```bash
sudo systemctl stop market-collector
```

### Stop All Instances
```bash
# SSH to each instance and run:
sudo systemctl stop market-collector
```

### Disable Auto-Start
```bash
sudo systemctl disable market-collector
```

### Emergency Shutdown
```bash
# Force kill if service won't stop
sudo systemctl kill -s SIGKILL market-collector
```
