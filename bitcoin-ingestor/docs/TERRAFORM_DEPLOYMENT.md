# Terraform Automated Deployment Guide

This guide explains how to deploy the complete market data ingestion system using Terraform, including automatic EC2 provisioning with 3 instances.

## Overview

The Terraform configuration automatically provisions:
- ✅ S3 bucket with encryption and versioning
- ✅ VPC with 3 public subnets (or use existing VPC)
- ✅ Security groups
- ✅ IAM roles and instance profiles
- ✅ **3x t3.micro EC2 instances**
- ✅ Automatic application installation and startup
- ✅ S3 VPC endpoint (cost optimization)

## Prerequisites

1. **AWS Account** with appropriate permissions
2. **AWS CLI** configured (`aws configure`)
3. **Terraform** installed (v1.0+)
4. **(Optional)** GitHub repository with your code

## Quick Start (5 Minutes)

### 1. Configure Terraform Variables

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
nano terraform.tfvars
```

**Minimal configuration** (just change bucket name):
```hcl
bucket_name = "my-market-data-raw-unique-name"
```

**Full configuration example**:
```hcl
aws_region  = "us-east-1"
environment = "prod"
bucket_name = "acme-market-data-raw-prod"

# EC2 Configuration
instance_count = 3
instance_type  = "t3.micro"
symbol         = "BTCUSDT"

# Networking (creates new VPC)
create_vpc = true
```

### 2. Deploy Infrastructure

```bash
# Initialize Terraform
terraform init

# Review plan
terraform plan

# Deploy (takes ~3-5 minutes)
terraform apply
```

Type `yes` when prompted.

### 3. Verify Deployment

After deployment completes, Terraform outputs important information:

```bash
# View outputs
terraform output

# Check instance IDs
terraform output collector_instance_ids

# Get private IPs
terraform output collector_instance_private_ips
```

### 4. Verify Data Collection

Wait 1-2 minutes for instances to fully start, then check S3:

```bash
# List recent files
aws s3 ls s3://your-bucket-name/raw/exchange=binance/stream=trade/symbol=BTCUSDT/ --recursive | tail -20
```

You should see files like:
```
2025-01-15 14:32:10  1048576  raw/.../part-i-0abc12345-1736950320567.jsonl.gz
2025-01-15 14:32:10     2048  raw/.../manifest-i-0abc12345-1736950320567.json
```

## Configuration Options

### EC2 Configuration

```hcl
# Number of instances (default: 3)
instance_count = 3

# Instance type (default: t3.micro, ~$7-8/month each)
instance_type = "t3.micro"

# Trading symbol to collect
symbol = "BTCUSDT"

# File rotation settings
rot_bytes = 2097152  # 2 MB
rot_secs  = 5        # 5 seconds
```

### Networking Options

**Option 1: Create New VPC (Default)**
```hcl
create_vpc              = true
vpc_cidr                = "10.0.0.0/16"
availability_zone_count = 3
associate_public_ip     = true
enable_s3_endpoint      = true  # Reduces S3 data transfer costs
```

**Option 2: Use Existing VPC**
```hcl
create_vpc = false
vpc_id     = "vpc-xxxxx"
subnet_ids = ["subnet-xxxxx", "subnet-yyyyy", "subnet-zzzzz"]
```

### Security Options

**SSH Access (Disabled by Default)**
```hcl
enable_ssh_access = true
ssh_cidr_blocks   = ["1.2.3.4/32"]  # Your IP only - NEVER use 0.0.0.0/0
```

**CloudWatch Logs (Optional)**
```hcl
enable_cloudwatch_logs = true
log_retention_days     = 7
```

### GitHub Repository (Optional)

If your code is in a GitHub repository:

```hcl
github_repo_url    = "https://github.com/your-org/market-data-ingestion.git"
github_repo_branch = "main"
```

If left empty, you need to manually deploy code (see Manual Deployment section).

## What Happens During Deployment

### Automatic Setup (User Data Script)

Each EC2 instance automatically:

1. **System Update**: Updates all packages
2. **Python Installation**: Installs Python 3.11
3. **Code Deployment**:
   - If `github_repo_url` is set: Clones repository
   - Otherwise: Creates directory structure
4. **Dependency Installation**: Installs boto3, websockets, etc.
5. **Configuration**: Creates `.env` file with instance-specific settings
6. **Service Setup**: Installs and starts systemd service
7. **Health Check**: Verifies service is running

### Instance Configuration

Each instance gets:
- **Unique Instance ID**: Used in filenames (e.g., `i-0abc12345`)
- **IAM Role**: Allows S3 PutObject access
- **Auto-restart**: Systemd service restarts on failure
- **Resource Limits**: 500MB memory, 80% CPU quota

## Monitoring & Management

### Check Instance Status

```bash
# Get instance IDs
INSTANCES=$(terraform output -json collector_instance_ids | jq -r '.[]')

# Check instance status
for id in $INSTANCES; do
  aws ec2 describe-instance-status --instance-ids $id
done
```

### View Logs (with SSH access enabled)

```bash
# SSH to instance
ssh -i your-key.pem ec2-user@<public-ip>

# Check service status
sudo systemctl status market-collector

# View logs
sudo journalctl -u market-collector -f

# View last 100 lines
sudo journalctl -u market-collector -n 100
```

### View Logs (without SSH - using CloudWatch)

If `enable_cloudwatch_logs = true`:

```bash
# Get log group name
LOG_GROUP=$(terraform output -raw cloudwatch_log_group)

# View recent logs
aws logs tail $LOG_GROUP --follow
```

## Verify Data Quality

### Check File Uploads

```bash
# Count files uploaded in last hour
aws s3 ls s3://your-bucket/raw/ --recursive | grep $(date -u +%Y-%m-%d) | wc -l

# Check manifest files
aws s3 cp s3://your-bucket/raw/.../manifest-xxx.json - | jq .

# Download and inspect data file
aws s3 cp s3://your-bucket/raw/.../part-xxx.jsonl.gz - | gunzip | head -5
```

### Expected Output

You should see approximately:
- **Files per hour per instance**: ~720 (5-second rotation)
- **Total files per hour (3 instances)**: ~2,160
- **Data per instance per hour**: ~700 MB compressed

## Scaling

### Change Number of Instances

```hcl
# Update terraform.tfvars
instance_count = 5  # Increase from 3 to 5

# Apply changes
terraform apply
```

New instances will start automatically.

### Change Instance Type

```hcl
# For higher volume
instance_type = "t3.small"

terraform apply
```

## Cost Estimation

### Monthly Costs (us-east-1)

| Component | Quantity | Monthly Cost |
|-----------|----------|--------------|
| EC2 t3.micro | 3 | ~$22.50 |
| S3 Storage (1TB) | - | ~$23.00 |
| S3 Requests (PUT) | ~50M | ~$0.25 |
| VPC (Internet) | - | $0.00 |
| **Total** | - | **~$46/month** |

**Cost Optimization**:
- Enable S3 VPC endpoint (included, saves data transfer fees)
- Use Glacier transitions for old data
- Consider t4g.micro (ARM, 20% cheaper)

## Troubleshooting

### Instances Not Starting

```bash
# Check instance system log
INSTANCE_ID=$(terraform output -json collector_instance_ids | jq -r '.[0]')
aws ec2 get-console-output --instance-id $INSTANCE_ID

# Look for user-data script errors
```

### Service Not Running

```bash
# SSH to instance
ssh -i your-key.pem ec2-user@<ip>

# Check user data completion
cat /var/log/user-data-completion.log

# Check service status
sudo systemctl status market-collector

# View service logs
sudo journalctl -u market-collector --since "10 minutes ago"
```

### No Files in S3

**Possible causes**:
1. Service not started - check systemd status
2. IAM permissions - verify instance role
3. Network issues - check security group allows outbound HTTPS
4. Binance API issues - check logs for connection errors

```bash
# Test S3 access from instance
aws s3 ls s3://your-bucket/

# Test Binance connectivity
curl -I https://stream.binance.com
```

## Manual Code Deployment

If not using GitHub repository, after Terraform deployment:

```bash
# SSH to each instance
for ip in $(terraform output -json collector_instance_public_ips | jq -r '.[]'); do
  echo "Deploying to $ip..."

  # Copy code
  scp -r ../../src ec2-user@$ip:/opt/market-data/market-data-ingestion/

  # Restart service
  ssh ec2-user@$ip "sudo systemctl restart market-collector"
done
```

## Cleanup (Tear Down)

To destroy all resources:

```bash
terraform destroy
```

**Warning**: This will:
- Terminate all EC2 instances
- Delete VPC and networking
- **NOT delete S3 bucket** (data preservation)

To delete S3 bucket too:
```bash
# Empty bucket first
aws s3 rm s3://your-bucket --recursive

# Then destroy
terraform destroy
```

## Multi-Symbol Deployment

### Deploy Multiple Trading Symbols

You can collect data for multiple symbols (BTCUSDT, ETHUSDT, SOLUSDT, etc.) simultaneously without interference.

**Quick Start:**

```bash
# Deploy BTCUSDT
cd infra/terraform
./deploy-symbol.sh BTCUSDT apply

# Add ETHUSDT (doesn't affect BTCUSDT)
./deploy-symbol.sh ETHUSDT apply

# Add more symbols
./deploy-symbol.sh SOLUSDT apply
./deploy-symbol.sh BNBUSDT apply
```

**Each symbol gets:**
- 3 dedicated EC2 instances
- Isolated Terraform state (workspace)
- Same S3 bucket, different path

**Cost:** ~$22.50/month per symbol (EC2 only)

**See [MULTI_SYMBOL_DEPLOYMENT.md](MULTI_SYMBOL_DEPLOYMENT.md) for complete guide.**

## Advanced Configuration

### Legacy: Use Multiple Symbols (Manual Workspaces)

Alternatively, deploy separate environments manually:

```bash
# BTCUSDT environment
terraform workspace new btcusdt
terraform apply -var="symbol=BTCUSDT"

# ETHUSDT environment
terraform workspace new ethusdt
terraform apply -var="symbol=ETHUSDT"
```

### Multi-Region Deployment

Create separate directories for each region:

```bash
cp -r infra/terraform infra/terraform-eu
cd infra/terraform-eu
# Update aws_region in terraform.tfvars
terraform init
terraform apply
```

## Next Steps

After successful deployment:

1. **Monitor for 24 hours** - Ensure continuous operation
2. **Set up Athena** - Query data directly in S3
3. **Create dashboards** - Monitor collection metrics
4. **Implement alerting** - Get notified of failures
5. **Plan data processing** - Add Lambda/Kinesis for real-time analytics

## Support

For issues:
- Check logs: `journalctl -u market-collector`
- Review user data: `/var/log/cloud-init-output.log`
- Terraform errors: `terraform apply` with verbose logging
- AWS support: Check EC2 system logs
