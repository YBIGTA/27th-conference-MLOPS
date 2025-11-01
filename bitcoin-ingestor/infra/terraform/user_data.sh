#!/bin/bash
set -e

# User data script for market data collector EC2 instances
# This script runs on first boot and sets up the collector application

echo "=================================================="
echo "Market Data Collector - Instance Setup"
echo "=================================================="

# Update system
echo "[1/8] Updating system packages..."
dnf update -y

# Install required packages
echo "[2/8] Installing Python 3.11 and dependencies..."
dnf install -y python3.11 python3.11-pip git

# Create application user
echo "[3/8] Creating application user..."
useradd -r -s /bin/bash -d /opt/market-data -m collector || true

# Clone repository or create application directory
echo "[4/8] Setting up application..."
cd /opt/market-data

%{ if repo_url != "" }
# Clone from Git repository
if [ ! -d "market-data-ingestion" ]; then
  sudo -u collector git clone ${repo_url} market-data-ingestion
  cd market-data-ingestion
  sudo -u collector git checkout ${repo_branch}
else
  cd market-data-ingestion
  sudo -u collector git pull
fi
%{ else }
# Create application structure manually
mkdir -p market-data-ingestion/src/collector
chown -R collector:collector market-data-ingestion
%{ endif }

cd /opt/market-data/market-data-ingestion

# Install Python dependencies
echo "[5/8] Installing Python dependencies..."
sudo -u collector python3.11 -m pip install --user \
  boto3>=1.34.0 \
  websockets>=12.0 \
  python-dotenv>=1.0.0 \
  pyyaml>=6.0

# Get instance ID
INSTANCE_ID=$(ec2-metadata --instance-id | cut -d " " -f 2)

# Create environment file
echo "[6/8] Creating environment configuration..."
cat > /opt/market-data/.env << EOF
RAW_BUCKET=${raw_bucket}
SYMBOL=${symbol}
LOCAL_DIR=${local_dir}
ROT_BYTES=${rot_bytes}
ROT_SECS=${rot_secs}
INSTANCE_ID=$INSTANCE_ID
AWS_REGION=${aws_region}
LOG_LEVEL=${log_level}
EOF

chown collector:collector /opt/market-data/.env
chmod 600 /opt/market-data/.env

# Create local data directory
echo "[7/8] Creating local data directory..."
mkdir -p ${local_dir}
chown collector:collector ${local_dir}
chmod 755 ${local_dir}

# Create and install systemd service
echo "[8/8] Setting up systemd service..."
cat > /etc/systemd/system/market-collector.service << 'SERVICEEOF'
[Unit]
Description=Market Data Collector for Binance
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=collector
WorkingDirectory=/opt/market-data/market-data-ingestion
EnvironmentFile=/opt/market-data/.env

# Use full path to user-installed packages
Environment="PATH=/opt/market-data/.local/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONPATH=/opt/market-data/market-data-ingestion"

# Start the collector
ExecStart=/usr/bin/python3.11 -m src.collector.collector_simple

# Restart policy
Restart=always
RestartSec=10
StartLimitBurst=5
StartLimitIntervalSec=300

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=market-collector

# Security settings
NoNewPrivileges=true
PrivateTmp=true

# Resource limits
MemoryMax=500M
CPUQuota=80%

[Install]
WantedBy=multi-user.target
SERVICEEOF

# Reload systemd and enable service
systemctl daemon-reload
systemctl enable market-collector.service
systemctl start market-collector.service

# Wait a moment and check status
sleep 5
systemctl status market-collector.service --no-pager

echo "=================================================="
echo "Setup Complete!"
echo "Instance ID: $INSTANCE_ID"
echo "Service Status:"
systemctl is-active market-collector.service
echo "=================================================="

# Log to CloudWatch (if CloudWatch agent is installed)
if command -v amazon-cloudwatch-agent-ctl &> /dev/null; then
  echo "CloudWatch agent detected, configuring..."
  # CloudWatch configuration would go here
fi

# Send completion signal
echo "Instance setup completed successfully at $(date)" >> /var/log/user-data-completion.log
